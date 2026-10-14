# Lines everywhere, and BettingPros (2026-10-14)

Read-only research for Devin. No orders, no credentials, no sign-ups, no purchases, no price pulls.
Web access was used for public documentation and public marketing pages only. Quotes are as returned by
a page fetch on 2026-10-14 (the fetch tool sometimes paraphrases; anything load-bearing should be
re-read on the page before a decision). Where a page could not be read, this note says so rather than guess.

Label (CLAUDE.md "advice or momentum"): *Devin's request* (Devin asked both questions). Not momentum.

## Disclosure (read first)

While counting books in `data/odds/odds_history.duckdb` (a COPY, read-only) I ran two aggregate queries that
did not filter `season <= 2024`, so they touched rows of season 2025 (the frozen holdout). What was read:
row counts per (book, market) and a min snapshot timestamp. No price, no outcome, nothing scored, no model run,
and nothing derived from those rows is used in this note. No HOLDOUT_ACCESS_LOG row was written first, which
non-negotiable 2 requires; a drafted disclosure row is at the end for Devin to append or reject. Every other
number below comes from `season <= 2024` or from non-historical sources.

## A. Lines everywhere

### A0. Bottom line

1. Do not add a vendor. The cheapest useful widening is already inside the plan Devin decided to keep:
   add `us_ex` to `regions` in `configs/odds_live.yaml` (Novig, ProphetX, Polymarket, Kalshi, BetOpenly in one
   schema, same key, same credits rule). Incremental plan cost: $0 (plan stays `START 5M`, $119/month).
2. The $30 ceiling and the $119 plan disagree. The ceiling ("monthly spend: very little (ceiling $30 until
   Devin raises it)", DECISIONS 2026-10-12) was written the same morning as "the-odds-api: START 5M kept"
   (DECISIONS 2026-10-12 08:26). Both are Devin's words. The data plan is $89 over the ceiling as stated. This is
   Devin's call; section A5 prices the in-ceiling alternative (the `20K` plan at $30, which is AT the ceiling,
   not under it).
3. A direct Polymarket adapter is premature: the public gateway needs no key, but nothing proves NBA player props
   are listed with ladders until opening night. Look through `us_ex` first; build an adapter only if the numbers
   justify it.
4. Kalshi direct stays the only free, per-tick, bid/ask source and the only one that can be a tradable price in
   our after-fee, after-spread EV. Everything through the Odds API is a displayed price, not a fillable one.

### A1. What we already do (checked in the repo, not assumed)

| Piece | Where | State |
|---|---|---|
| Kalshi read-only snapshots | `nba/kalshi`, `configs/kalshi.yaml`, launchd every 15 min into `data/kalshi/kalshi.duckdb` | running; last run 2026-10-14 17:09Z, 370 open markets |
| Kalshi series ingested | `game_series: [KXNBAGAME, KXNBASPREAD, KXNBATOTAL, KXNBATEAMTOTAL]`; `prop_series: [KXNBAPTS, REB, AST, 3PT, PRA, PR, PA, RA, STL, BLK]` | props mapped only for pts/reb/ast/3pt (`kalshi_map.py`); PRA/PR/PA/RA/STL/BLK/TEAMTOTAL are parsed but reported `unsupported_series_or_stat` |
| the-odds-api.com live capture | `configs/odds_live.yaml`, `nba/odds/live_close.py`; base `https://api.the-odds-api.com/v4`; env `THE_ODDS_API_KEY` | 89 markets x 2 regions (`us`, `eu`) per call, 12 snapshot points per game; first live raw file expected opening night 2026-10-20 (NEXT_SESSION); no live raw directory exists yet |
| the-odds-api.com history | `configs/odds_history.yaml`, `data/odds/odds_history.duckdb` | 11.2M rows (all seasons incl. holdout rows); player props 2023, 2024 from 17 books incl. Pinnacle; Novig and ProphetX from 2024-12-20; NO `*_alternate` player markets in the store (never pulled) |
| theoddsapi.com (different vendor) | `configs/odds.yaml`, `nba/odds/theoddsapi.py` | subscription cancelled 2026-10-10; adapter kept; do not confuse the two vendors (memory: vendor-names-exact) |

Credit arithmetic as configured (upper bound; the vendor bills markets RETURNED, so a book that posts no
alternates costs less): 89 markets x 2 regions = 178 credits per call; x 12 points = 2,136 per game; x 215 peak-month
games = 459,240 credits, 9.2% of 5,000,000. Source of the rule: vendor v4 guide, "`/events/{id}/odds`: cost =
[number of unique markets returned] x [number of regions specified]. Empty responses don't count", and
"`/events`: This endpoint does not count against the usage quota"
(https://the-odds-api.com/liveapi/guides/v4/).

Names and aliases: reviewed table `configs/kalshi_aliases.yaml` (110 lines), generated table
`configs/odds_player_aliases.yaml` (1,023 lines, normalised exact match only, collisions unresolved),
`UnmatchedKalshiNameError` fails loud. Any new source goes through the same two tables; nothing in this note
proposes guessing. Candidate aliases would go to a dated candidates file, never straight into the live table.

### A2. Kalshi (direct)

- **Which NBA markets exist now.** From our own store (read-only copy, 2026-10-14): open `KXNBAGAME` 48,
  `KXNBASPREAD` 175, `KXNBATOTAL` 147 (370 markets). No prop series is open. Props seen historically: `KXNBAPTS`
  124 markets / 5 events and `KXNBAREB` 100 / 3 events, i.e. Finals 2026 and two or three preseason games
  (docs/KALSHI_READINESS_2026-10-09.md section 2: props post game day, close to tip). `KXNBAAST/3PT/PRA/STL/BLK` never seen.
- **Alt-line ladders.** Kalshi has no "alternate line" product; every strike is its own market. Spread and total
  already arrive as ladders (e.g. 175 spread markets over about 50 events). Player props are N+ thresholds, one
  market per threshold per player. A third-party review says several thresholds per player exist, citing
  "separate De'Aaron Fox contracts for 5+, 6+ and 8+ assists" in May 2026
  (https://www.si.com/prediction-markets/reviews/kalshi-nba). Our scout note (docs/research/historical_odds_2026-10-09.md)
  saw about 5 players x 5 thresholds per Finals game. Whether the 2026-27 regular season lists more players is
  unknown until 2026-10-20; the first-night check is `python -m nba.kalshi discover` plus the alias review.
- **Latency/cost.** Free. Our poll is 15 minutes (launchd; Mac sleep loses slots, about 2% of runs failed).
  Documented rate limits are token budgets: "Basic 200 read / 100 write tokens per second", most requests 10
  tokens (https://docs.kalshi.com/getting_started/rate_limits); we use `rate_limit_s: 0.25` with 14 series
  per run, far under that.
- **Public access.** "No authentication headers are required for the endpoints in this guide"
  (https://docs.kalshi.com/getting_started/quick_start_market_data), which also documents the production host as
  `https://external-api.kalshi.com/trade-api/v2` and says "Despite the 'elections' subdomain, the production Trade
  API provides access to ALL Kalshi markets." Our `configs/kalshi.yaml` uses `https://api.elections.kalshi.com/trade-api/v2`.
  The page does not call the old host deprecated; flag as a watch item, not a change (the snapshot job works today).
- **Fit.** Native. It is the only source whose price we could actually trade, so it is the only one used for EV after fee
  and spread. Fees stay a verified config object.

### A3. Polymarket

- **US access status 2026.** Secondary sources only (not verified against a primary announcement): Sacra reports
  Polymarket acquired the CFTC-licensed exchange QCEX (July 2025) and "launched in America in May 2026 on iOS" with sports as the
  first category (https://sacra.com/research/polymarket-at-375m-year-revenue/); a guide says Polymarket US operates through QCX LLC, a
  CFTC-designated contract market (https://ats.io/prediction-markets/polymarket/legal/). A review dated 2026-10-14
  refers to the "Polymarket US App", says "Not available in all jurisdictions", and says NBA game pages can carry winner,
  spread, total and player points/rebounds/assists markets (https://www.si.com/prediction-markets/reviews/polymarket-nba).
  Primary: the US docs FAQ lists contract types "Winner (w/o ties)", Spread, Total, Future, Qualifier and "Player Prop"
  ("Whether a player's stat reaches a threshold, such as LeBron scoring over 25.5 points")
  (https://docs.polymarket.us/faqs/sports-faqs.md). Eligibility by state is not stated there; unknown.
- **What read-only public data allows.** Two separate stacks.
  - Polymarket US: "Public API: https://gateway.polymarket.us ... No API key needed" for Markets, Events, Series, Sports,
    Search; "List markets, get order books, BBO, settlement prices, and price history"; the authenticated host
    `https://api.polymarket.us` is for orders/portfolio/account (https://docs.polymarket.us/api-reference/introduction).
    NBA events: `https://gateway.polymarket.us/v2/leagues/nba/events`
    (https://docs.polymarket.us/data-guide/sports-data.md). Rate limit: "The Retail API has a general limit of 25
    requests/s ... Exceeding a limit returns 429" (https://docs.polymarket.us/api-reference/rate-limits.md).
  - Polymarket international: Gamma `https://gamma-api.polymarket.com` and CLOB `https://clob.polymarket.com`
    with documented IP rate limits (e.g. "/book, /price, /midpoint: 1,500 per 10 seconds"); docs do not state an auth
    requirement for reads (https://docs.polymarket.com/api-reference/rate-limits). Order placement has a geographic
    restriction page (`/api-reference/geoblock`); whether US residents may use the international site's data is not
    something I could verify.
- **Alt lines/ladders.** Not documented. The sports-data guide does not describe player-prop event/market schema,
  lines or thresholds. "Unknown until a live NBA event is read."
- **Fees (documented, not for hardcoding).** Fees page: standard taker formula `Fee = Theta x C x p x (1 - p)`,
  taker Theta 0.0695, maker rebate -0.0125, maker rebate tiers change 2026-10-19
  (https://docs.polymarket.us/fees.md). Any use here would be a verified config entry with this URL, not a literal.
- **Name matching.** `/v1/sports/players` returns `providerIds` (SportsDataIO, Sportradar, OpticOdds, ...). None is
  confirmed to be an NBA stats id, so we would still map by reviewed alias; it may offer a stable cross-check
  (https://docs.polymarket.us/api-reference/sports/get-sports-players.md).
- **Via the Odds API instead.** `us_ex` lists `kalshi`, `novig`, `polymarket`, `prophetx`, `betopenly`
  (https://the-odds-api.com/sports-odds-data/bookmaker-apis.html). Our own history shows what `us_ex` actually
  returned for NBA player props in seasons <= 2024: Novig (season 2024: 914 games with props, from 2024-12-19; points,
  rebounds, assists, threes) and ProphetX (season 2024: 926 games, same markets); no Kalshi or Polymarket
  props in seasons <= 2024. Whether `us_ex` now returns Polymarket or Kalshi NBA props is something the first live pull
  answers for the price of the extra region.
- **Fit.** An adapter would live beside `nba/kalshi` as a second read-only price source behind the same
  `(ticker, ts, source)` key. Not worth building until Section A5 step 1 shows listings.

### A4. The Odds API (the-odds-api.com)

- **Plan we have (repo record).** `START 5M`, $119 per month, 5,000,000 credits (configs/odds_live.yaml `plan:`;
  DECISIONS 2026-10-10, 2026-10-12 08:26). Vendor pricing page, fetched today: Starter free 500 credits; 20K $30; 100K $59;
  5M $119; 15M $249 (https://the-odds-api.com/). Note: the page labels differ from the repo's "START 5M" (the repo
  name could be a plan label from the checkout); confirm the exact label on the account page before any change. The
  server-reported usage on 2026-10-11 was 1,558,598 used and 3,441,402 remaining (ledger
  `data/odds/budget/the_odds_api_live_2026-10-11.json`), i.e. the history pull is billed in this period.
- **Books.** `us`: betonlineag, betmgm, betrivers, betus, bovada, williamhill_us (Caesars), draftkings, fanatics,
  fanduel, lowvig, mybookieag. `us2`: ballybet, betparx, espnbet, fliff, hardrockbet, and others. `us_ex`: betopenly,
  kalshi, novig, polymarket, prophetx. `eu`: includes pinnacle ("Odds are from public website which may incur a delay")
  (https://the-odds-api.com/sports-odds-data/bookmaker-apis.html). The pinnacle note matters: it is a benchmark with a
  possible delay, not a tick feed.
- **Player props and alternate lines.** Yes. Main: `player_points, player_rebounds, player_assists, player_threes,
  player_blocks, player_steals, player_blocks_steals, player_turnovers, player_points_rebounds_assists, ...`.
  Alternate (milestone X+ lines): `player_points_alternate, player_rebounds_alternate, player_assists_alternate,
  player_blocks_alternate, player_steals_alternate, player_turnovers_alternate, player_threes_alternate,
  player_points_assists_alternate, player_points_rebounds_alternate, player_rebounds_assists_alternate,
  player_points_rebounds_assists_alternate` (https://the-odds-api.com/sports-odds-data/betting-markets.html). Our 89-market
  list matches this page. Which books post alternates for which players is not documented there; first live night will say.
  The page also says player props are "mainly limited to US sports and US bookmakers", requested one event at a time.
- **Latency.** The vendor guide gives no general refresh rate for props (only suspended markets disappear "after
  about 15 minutes"). Our schedule is snapshot-based (12 points before tip), so we measure staleness ourselves from
  each row's `last_update`; this should be reported at the first look rather than assumed.
- **Cost per market.** 1 credit per market per region on the live per-event endpoint; 10 on the historical one;
  `/events` and `/sports` free.
- **Fit.** Already built: raw-first gzip, credit ledger refusing before the cap, key stripped from every log, GET-only
  paths. These are displayed prices of consumer books and exchange order books, not fillable prices for us.

### A5. Recommendation and monthly cost against the $30 ceiling

All credit figures are upper bounds at 215 games/month.

| Option | Config | Credits/mo | Plan | $/mo | Vs $30 |
|---|---|---|---|---|---|
| 0. As decided (Devin 2026-10-12) | 89 markets, `us,eu`, 12 points | 459,240 | 5M | 119 | +89 |
| 1. **Recommended** (if the data plan stays) | option 0 + `us_ex` (89 x 3 regions = 267/call) | 688,860 (13.8% of 5M) | 5M | 119 (+0) | +89 |
| 2. In-ceiling fallback | 11 markets (3 game, 4 main player, 4 `*_alternate` pts/reb/ast/3pt), `us,eu`, 3 calls per game (T-180, T-60, close) = 66/game | 14,190 (71% of 20K) | 20K | 30 | at ceiling |

1. Recommended step now: option 1 (flip `regions` to include `us_ex`). It costs credits inside an already-paid
   allowance, not dollars. The cost guard in `LiveConfig` (`max_plan_fraction: 0.5`) still passes (13.8%). This needs
   Devin's go: it changes a production capture config.
2. Opening night: read the first `us_ex` payload before building anything: which of novig/prophetx/polymarket/kalshi
   return NBA player props, and with how many points each. That is the data Polymarket and alt-line decisions need.
3. Only if (2) shows Polymarket or Kalshi NBA props returned with ladders: consider a direct adapter for Polymarket
   (free, 25 req/s, no key). Cost $0 dollars; cost is engineering plus alias review.
4. If the $30 ceiling binds: option 2. It loses 8 of 12 snapshot points and the period/special markets (the 2026-10-12
   widening) but keeps main plus alternate player lines and Pinnacle. Also note dropping to 20K ends the room for any
   historical pull (10x credits).
5. What not to buy: nothing. No second vendor adds a source that `us`/`eu`/`us_ex` does not already cover. The scout's SKIP list
   (OpticOdds, OddsJam, SportsGameOdds $299 and up) stands (docs/research/historical_odds_2026-10-09.md).
6. Honest framing: none of these sources changes the verdict that props are the only plausible edge and it is probably
   small. More lines is more benchmark, not more edge.

### A6. Terms on automated read access (as found today)

| Source | What I could read | What it says | Verdict for our use |
|---|---|---|---|
| Kalshi | API docs and quickstart. The Developer Agreement and Member Agreement pages returned HTTP 429 / unreadable PDF; text NOT read | Docs: "By continuing to use or access Kalshi's API, you are agreeing to be bound to our Developer Agreement" (https://docs.kalshi.com/welcome, agreement at https://kalshi.com/developer-agreement); "No authentication headers are required" for market-data endpoints (quickstart above) | Public GETs are the documented path and we are polite (0.25 s spacing, retry with backoff). Binding terms unread: Devin should read the Developer Agreement once; I did not find a clause on storage or redistribution. Unverified. We do not redistribute |
| Polymarket US | API docs readable. `polymarket.us/tos` and `polymarket.us/terms` rendered only a title and sign-up prompt; text NOT read | "No API key needed" for the public gateway; the footer text is "By continuing you agree to Polymarket's terms and privacy policy"; rate limit 25 req/s with 429 | Public GETs are the documented path. ToS clauses on automation/data reuse and state eligibility unread. Unverified. Rate-limit rule: honour 429 and back off |
| Polymarket international | Rate-limit page readable; ToS not found | IP-based limits; "Check where order placement is available" with a geoblock page | Not recommended for us: jurisdiction question unanswered |
| The Odds API | Terms page content partly returned | "Do not resell, repackage, or redistribute our data as a standalone data product"; commercial use allowed "provided our data is not the primary product being sold or redistributed"; "Storing our data and retaining it indefinitely" and "Using our data to train statistical and machine learning models" are listed as permitted; "It is your responsibility to keep your API key private"; "We reserve the right to revoke your API key and block future access" on suspected violation; no clause found on rate limits (https://the-odds-api.com/terms-and-conditions.html) | Explicitly permits our use (storage, model training). Keep: no raw-feed redistribution. A site that shows displayed odds to users (APP) should stay derived and attributed, not a book-price feed; confirm with the page before launch |

## B. BettingPros

### B1. Can we scrape them? (answer first)

**No, and this note does not rest on their ToS text, because I could not read it.** What was and was not found:

- `https://www.bettingpros.com/terms/` returned "only navigation, a footer, and a disclaimer" (footer link
  "Terms of Use" to `/terms/`); `/terms-of-use/` and `/terms-of-use` returned HTTP 404; the archive mirror is
  not reachable from this environment. A search for the clause text found only generic commentary, not their terms.
  So no scraping, automation, reuse or commercial-use clause could be quoted. Unverified.
- `https://www.bettingpros.com/robots.txt` has one rule group, `User-agent: *`, and disallows `/api/`, `/u/`,
  `/template/`, `/contest-app/`, `/upgrade/`, and article admin paths; no rule for `/nba/` pages. The robots file is
  an etiquette signal, not terms, but `/api/` is exactly the data endpoint a scraper would hit.
- They sell that data: "BettingPros API ... The sports betting API with the model built in", JSON, "20+ sportsbooks and
  prediction markets, one schema", tiers "Free to explore, Plus for ungated picks, Elite for higher limits"; "Free returns
  the same prop with the picks gated". No dollar amounts and no license terms on the page
  (https://www.bettingpros.com/api-data/). The page footer: "The content of this website is intended to be used for
  entertainment purposes only."
- Our earlier scout already ruled it out: "Scraping typically prohibited by ToS (BettingPros terms not located)"
  (docs/research/historical_odds_2026-10-09.md).

So: we will not scrape; the only sanctioned route is their paid API, whose license we have not seen, and buying it is
Devin's decision with the exact URL and plan first. Nothing in this note proposes it. Our own lines (Kalshi,
the-odds-api) already cover the consumer books they aggregate.

### B2. What BettingPros offers (summarised, not copied)

| Item | What the public pages say |
|---|---|
| Plans | "Plans from $9.99/month"; tiers BettingPros Plus and Elite; "Picks, projections and edge on every line, plus Sharp AI and Betting Systems on Elite" (https://www.bettingpros.com/premium/). Annual price and per-tier prices not shown |
| Prop cheat sheet | "compares lines from leading sportsbooks with our daily NBA projections"; no projection numbers shown to the free reader; "Access via API", "Sharp AI", "Premium" and "Leaderboards" links (https://www.bettingpros.com/nba/picks/prop-bets/) |
| Prop analyzer / ratings | Support centre (via search results): each prop gets "a BettingPros projection ... along with our rating of whether the prop is worth betting"; premium adds "Cover Probability, Expected Value and Star Rating"; the star-rating method is not described (https://support.bettingpros.com/hc/en-us/articles/7578882204699-What-is-Expected-Value, snippets) |
| EV, projection-based | "This metric leverages our projections and factors in the sportsbook odds"; EV formula "(probability of winning x amount won) minus (probability of losing x amount lost)"; "Cover probability ignores the vig, while EV includes it" |
| EV, market-based tool | "It uses the market itself as the source of truth"; "When you combine the consensus from these sharp books, you get the closest thing to 'true' odds that exists in the market"; "A 5% edge means the odds you're getting are roughly 5% better than they should be based on sharp consensus". Sharp books not named; Pinnacle not mentioned; vig removal not described; no ROI or win rate given (https://www.bettingpros.com/articles/bettingpros-launches-market-based-ev-tool/) |
| Records | No win-rate or accuracy figure on the NBA prop page fetched; a "Leaderboards" link only. `Betting Systems` reliability article was not readable (HTTP 403) |

### B3. How high claimed EV arises: claim, pro, con

| Claim mechanism | Why it can be real | Why the number is overstated | Evidence | What it means for us |
|---|---|---|---|---|
| Soft book price vs no-vig sharp consensus | A soft book that lags the sharp consensus is a genuine edge on that price | Edge exists only while the line is stale and you can bet it at size; the tool's own article defines edge as price better than consensus, "5% ... roughly 5% better", not realised return; consensus quality depends on unnamed sharp books | BettingPros market EV article above; "A scanner's help page says stale lines vanish fast (often <10 min)" (https://edge-scanner.duckdns.org/help, a third-party tool, anecdotal) | A displayed edge at snapshot time is not a fillable edge. We score against the price at prediction time and never the close |
| Stale lines | Books do lag after news | Alerts expire before you can act; books limit accounts that harvest stale lines | Search-result summaries: sportsbooks "limit bettors who consistently win or beat closing lines" (BetSmart newsletter, https://betsmart.beehiiv.com/p/sportsbooks-limit-winning-bettors; anecdotal, not policy) | Limits and fills are not modelled by any "EV%" headline. We report spread-inclusive EV and no stakes |
| Ignoring limits and line movement | none | An EV% implicitly assumes unlimited stake at that price and no move before fill; BettingPros' +EV article: "It doesn't discuss stake sizing, bankroll management, or sportsbook limits" (their article, as summarised by fetch) and claims +EV "is significantly less likely to get your accounts limited" without explanation | https://www.bettingpros.com/articles/what-is-ev-betting-the-strategy-behind-long-term-profitability/ | State n, CI, and "limits unknown" next to any edge |
| Projection vs market (projection-based EV) | A good projection beats a market | The EV is computed from their own probability, so a miscalibrated or over-dispersed projection manufactures large EV on the biggest disagreements (winner's curse: ranking by disagreement selects the model's errors); the support text says EV "leverages our projections" and gives no calibration or track record | Support article above; our own replay: the market beat our game-winner forecast by Brier 0.0064 (CLAUDE.md) | Report a model-vs-price score with n and CI before any EV; below 30 settled markets, "insufficient sample" |
| Survivorship / selection in shown records | Honest records exist | A shown record is chosen among many; tipsters who lose stop posting. A 2026 study tracking 5,467 slips from three Nigerian social-media tipsters found "influencers collectively lost 25.24%" on promoted bets and a same-stake follower would lose "38.27%", noting survivorship bias when influencers share only winners (https://arxiv.org/pdf/2604.08251; different market, secondary-source summary). A simple model: 10,000 tipsters with 50% winning-year chance leave about 313 with five straight winning years by luck (https://www.wettbasis.com/?p=43757; illustration, not a study) | Different country and book, so not a measurement of BettingPros | Never show a hit record without the denominator; "this one hit; it was still a -X% bet" |
| Parlays: fees and correlation ignored | Correlation can be mispriced; one vendor reports the same two-player parlay at +203 (FanDuel) vs +252 (DraftKings) (https://opticodds.com/blog/correlation-in-same-game-parlays, vendor marketing) | Each leg carries its own vig and probabilities multiply, so errors compound; hold estimates vary (6-8% for two legs, 15-25% for four-leg SGPs in one guide, 25-40% of dollars wagered in an operator-economics vendor note: https://track360.io/blog/same-game-parlay-sgp-operator-economics-affiliate-attribution-2026), all marketing; the only peer-style paper found (UWF, college football 2005-2015) argued books were too conservative on correlation (https://ircommons.uwf.edu/esploro/outputs/journalArticle/Correlated-parlay-betting/99380090298406600) | A single-leg +EV scanner does not price an SGP; their stack is independent of our copula | Our rule: same-game dependence modelled by the copula (28.6% vs 20.1% hit rate for same-player legs), cross-game residual r about 0 (verified by `independence_check`), fees from a verified config object, calibration reported per input |
| "+EV is the only strategy that works long term" | Positive expectation does win in the limit | Needs "several hundred bets minimum" by their own FAQ, and edges of "5-8%" are the author's typical selected edges, not realised returns (their market-EV article) | BettingPros articles above | We will not show a profitability claim without n and a clustered CI |

### B4. What we can learn or offer honestly

- Learn: the market-consensus idea is the same as our "markets are a prior and a benchmark", and the vendor
  language ("the market itself as the source of truth") is a clean statement of why we defer to price. Their cheat-sheet
  idea (ranked edges) is what our `evaluate` already produces, but with a spread-inclusive EV, an interval, and a verdict.
- Do not copy: their layout, ratings, wording or any behind-paywall content. Ours are our own computations.
- Offer: (1) `no_positive_ev_found` as a first-class answer; (2) every number with n and a clustered CI; (3) displayed
  "true odds" identically after wins and losses; (4) a hit labelled "this one hit; it was still a -X% bet"; (5) fees
  and spread in EV; (6) no stakes or sizing prompts; (7) the prior record of the market beating us on game winners,
  stated plainly. "Keep your money" is the default until the live log proves skill.
- Differentiator that is true and checkable: they show EV%; we show whether the sample is big enough to believe any EV%.

## C. Unknowns and what needs Devin

1. Plan vs $30 ceiling (A5). Keep the $119 plan, or take option 2 at $30.
2. Flip `us_ex` into `configs/odds_live.yaml` (production capture config; my ownership lets me edit it, but it is
   Devin's call and untested live). Not changed in this branch.
3. Read the Kalshi Developer Agreement and Polymarket US ToS (both unreadable here) once; add the clause numbers to
   this note.
4. Which books post alternates, and whether `us_ex` returns Polymarket or Kalshi NBA props: first live pull on 2026-10-20.
5. BettingPros terms text: unreadable; no scraping, with or without it.

## D. Drafted rows (not appended)

Holdout log (drafted, for Devin to append or reject; this is a disclosure, not an access request):
`| 2026-10-14 | market agent (research) | read-only aggregate counts on a copy of data/odds/odds_history.duckdb including season 2025 rows | unfiltered group-by queries (book, market counts; min snapshot_ts); no prices, outcomes, or models; nothing used downstream | disclosed after the fact; query lacked season <= 2024 | n/a |`

DECISIONS draft (if Devin accepts A5 step 1): "2026-10-14 | add `us_ex` to live `regions` | Novig/ProphetX/Polymarket/Kalshi book prices in the same schema for +89 credits/call (13.8% of plan at peak), inside the already-kept plan | the first live `us_ex` payload returns no NBA props, or the plan is changed |".

## Sources

- https://the-odds-api.com/ , https://the-odds-api.com/liveapi/guides/v4/ ,
  https://the-odds-api.com/sports-odds-data/bookmaker-apis.html ,
  https://the-odds-api.com/sports-odds-data/betting-markets.html , https://the-odds-api.com/terms-and-conditions.html
- https://docs.kalshi.com/getting_started/rate_limits , https://docs.kalshi.com/getting_started/quick_start_market_data ,
  https://docs.kalshi.com/welcome , https://kalshi.com/developer-agreement (not readable)
- https://docs.polymarket.us/ , https://docs.polymarket.us/api-reference/introduction , .../rate-limits.md , .../fees.md ,
  .../faqs/sports-faqs.md , .../data-guide/sports-data.md , .../api-reference/sports/get-sports-players.md ,
  https://docs.polymarket.com/api-reference/rate-limits , https://docs.polymarket.com/api-reference/introduction
- https://www.si.com/prediction-markets/reviews/polymarket-nba , https://www.si.com/prediction-markets/reviews/kalshi-nba ,
  https://sacra.com/research/polymarket-at-375m-year-revenue/ , https://ats.io/prediction-markets/polymarket/legal/
- https://www.bettingpros.com/robots.txt , /api-data/ , /premium/ , /nba/picks/prop-bets/ , /terms/ (empty body) ,
  /articles/bettingpros-launches-market-based-ev-tool/ , /articles/what-is-ev-betting-the-strategy-behind-long-term-profitability/ ,
  https://support.bettingpros.com/hc/en-us/articles/7578882204699-What-is-Expected-Value (search snippets only)
- https://arxiv.org/pdf/2604.08251 , https://www.wettbasis.com/?p=43757 , https://opticodds.com/blog/correlation-in-same-game-parlays ,
  https://track360.io/blog/same-game-parlay-sgp-operator-economics-affiliate-attribution-2026 ,
  https://ircommons.uwf.edu/esploro/outputs/journalArticle/Correlated-parlay-betting/99380090298406600 ,
  https://betsmart.beehiiv.com/p/sportsbooks-limit-winning-bettors , https://edge-scanner.duckdns.org/help
- Repo: docs/KALSHI_READINESS_2026-10-09.md, docs/research/historical_odds_2026-10-09.md, docs/DECISIONS.md
  (2026-10-10, 10-11, 10-12 entries), configs/odds_live.yaml, configs/odds_history.yaml, configs/kalshi.yaml, docs/NEXT_SESSION.md
