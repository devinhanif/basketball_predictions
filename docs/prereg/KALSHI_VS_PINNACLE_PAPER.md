# KALSHI_VS_PINNACLE_PAPER — is a soft price cheaper than Pinnacle's de-vigged fair value after fees? (paper only)

Status: **DRAFT, not frozen** (freezing is a later step by another hand). Branch agent/market-kalshi-pinnacle.
Forward and historical, paper only (non-negotiable 5): no orders, no credentials, no execution code. Devin's idea
(2026-10-22: "go for it"): treat Pinnacle's de-vigged price as fair value; buy where a soft price is cheaper than
fair by a margin. Thresholds below were chosen by Claude from label-free counts only (no settlement, outcome or
return read): research/scratch/kalshi_pinnacle_gate_2026_10_22.py and research/scratch/softbook_pinnacle_hist_gate_2026_10_22.py.

Lineage: ODDS_HISTORY measured model vs Pinnacle (market better, T203-T206). This asks the reverse question: is a
soft price ever off Pinnacle by more than the soft venue's cost? Prior: low. Pinnacle is the sharpest book we hold;
soft-book quotes lag it by minutes, which is the only plausible source of edge. Expected answer: keep your money.

## Stage A — HISTORICAL (runs first; gates how Stage B is read)
Data: `data/odds/odds_history.duckdb`, table `odds_history` (the ODDS_HISTORY pull; the-odds-api historical).
`season` in SQL is `<= 2024` always (2023 = 2023-24 = SELECT season; 2024 = 2024-25 = REPORT season). Season 2025
is never loaded. Snapshot kind `t60` is primary (stored lead 64.4 min median, a true T-60); `t5` is reported only.
Outcomes (for returns) come from `player_game_stats` joined on (game_id, player_id), `season <= 2024`, read only
after this rule is frozen.

- **Unit / pairing**: markets player_points, player_rebounds, player_assists, player_threes. A line is
  (season, game_id, snapshot_kind, market, player_id, point) with BOTH an over and an under price at one book. A
  retail line is matched to Pinnacle only on the SAME game, SAME snapshot_kind (same requested time), SAME market,
  player and EXACT `point`. Only half lines (point not an integer) are used: whole-number lines are EXCLUDED (a push
  would refund; excluding avoids a pricing convention). Gate: Pinnacle posts zero whole lines in 2023-24/2024-25 (0 of
  ~154k), so nothing is lost from the Pinnacle side; retail whole lines (all books) never match and are dropped.
- **Fair value**: Pinnacle two-way proportional de-vig from raw implied probabilities of the American prices,
  `fo = ro/(ro+ru)`; both sides required (median Pinnacle overround 6.99%).
- **Bet**: at retail book B's posted price, exact fractions. OVER if `ro_B < fo − m`; UNDER if `ru_B < (1−fo) − m`.
  (ro_B = B's raw implied probability of that side, vig included.) At most one bet per (book, line, side); both
  sides cannot both qualify when m ≥ 0.
- **Return** per $10 stake: win at American price a>0: `+10·a/100`; a<0: `+10·100/|a|`; loss `−10`. Over wins iff
  stat ≥ ceil(point); under wins iff stat < point. A DNP (minutes 0 or NULL) is excluded and counted (books void).
- **Books**: BH family = the 7 retail books quoting in BOTH seasons with ≥ 20,000 matched half lines at T-60 per
  season: draftkings, fanduel, betmgm, williamhill_us, betrivers, bovada, betonlineag. Excluded and reported only:
  fanatics (3.7k lines), pointsbetus/unibet_us/mybookieag (2023 only), novig/prophetx (exchanges), kalshi.
- **m grid (fixed)**: {0.02, 0.03, 0.04, 0.05}. **Selection (2023-24 only)**: the m with the highest pooled mean
  return per bet over the 7 books among m with ≥ 500 bets and ≥ 100 games; if no m has mean return > 0 the stage is
  NULL and the report season is NOT scored for return. Selection uses no 2024 row.
- **Report season (2024)**: at the selected m, pooled over the 7 books. Statistic = mean return per bet.
  Inference: percentile CI, resample game dates with replacement (10,000, default_rng(20261022); the date is
  `games.game_date`); also the game-cluster percentile CI (resample game_ids) and the per-date t-interval
  (df = dates − 1). Per-book mean and one-sided date-cluster p (share of resamples ≤ 0), BH at q = 0.05 across the 7
  books.
- **Information-matched placebo (P1)**: same rule, m and inference, but fair value = another retail book's own
  de-vig (bet at DraftKings with FanDuel's de-vig as fair, and at FanDuel with DraftKings's) instead of Pinnacle. A
  stale quote can still lag, but no sharper information enters, so P1 should not clear zero. If P1's date-cluster
  lower bound > 0 the PASS is voided as "edge is quote staleness shared by any pair, not Pinnacle information".
- **PASS (report season)**: pooled mean return > 0 with BOTH the date-cluster percentile lower bound > 0 and the
  per-date t lower bound > 0, AND ≥ 1 of the 7 books BH-significant with mean > 0, AND P1 not voiding.
  **HARM**: pooled date-cluster CI wholly < 0. **INCONCLUSIVE**: < 300 report-season bets or < 30 dates.
  **NULL**: otherwise. The verdict applies to the displayed price only (the book's vig is already in the price; no
  separate fee; no stake limits, no account limiting: a PASS is an upper bound on what is bettable).
- **Meaning for Stage B**: Stage A PASS is required before a Stage B PASS counts as confirmation. Stage A NULL or
  HARM: Stage B is recorded as exploratory only. Stage A INCONCLUSIVE: same as NULL.
- **Reported, no decision weight**: by stat, side, book; T-5 version (same rule at `t5`); distance bins of edge;
  a line-move analog (see below); realised hit rate vs mean fair vs mean book price.

### Stage A gate (label-free, non-negotiable 8; prices only, no outcome, no return)
Retail-book matched half lines at T-60 per book are 25.9k-34.3k (2023) and 25.9k-35.1k (2024) for the seven books,
1.1k-1.25k games each. Pooled, all-10-book exploratory counts of qualifying bets at T-60:

| m | 2023 overs | 2023 unders | 2023 bets | 2024 overs | 2024 unders | 2024 bets |
|---|---|---|---|---|---|---|
| 0.01 | 6,527 | 9,830 | 16,357 | 5,253 | 5,258 | 10,511 |
| 0.02 | 2,411 | 3,451 | 5,862 | 1,975 | 1,997 | 3,972 |
| 0.03 | 707 | 1,037 | 1,744 | 628 | 657 | 1,285 |
| 0.04 | 195 | 291 | 486 | 205 | 217 | 422 |
| 0.05 | 57 | 87 | 144 | 74 | 85 | 159 |

Per-book bets at m=0.03 (2023 / 2024): betrivers 415/396, fanduel 236/207, draftkings 149/173, betmgm 102/204,
bovada 115/210, williamhill_us 112/55, betonlineag 50/38. The selection floor (≥ 500 bets) is cleared on 2023 by
m ≤ 0.04 (all 10 books pooled: 486 at m = 0.04 is just under, so for the seven books m = 0.04 likely fails it and
the selected m is effectively 0.02 or 0.03). Games with a qualifying bet at 0.03:
652 (2023), 647 (2024). Placebo P1 volume at m=0.03: 31-288 bets per direction per season, enough to run.
Pooled-book counts are not independent (one line quoted by several books); the clustering handles it.
Per-book power is thin at m ≥ 0.04 (a few dozen bets): per-book results there are descriptive.

**MDE arithmetic** (no returns computed): a bet near even odds has SD ≈ $9.7 per $10. Pooled m=0.03, 2024: n ≈
1,285 bets (~650 games); with a design effect ≈ 1.3 from same-game/same-date bets, SE ≈ 9.7·√1.3/√1285 ≈ $0.31,
MDE (80% power, one-sided 5%) ≈ 2.5·SE ≈ **$0.78 per $10 (≈ 7.8% ROI)**. The naive best case, if Pinnacle's fair
is exactly the truth, is ≈ 10·m/p ≈ $0.6 per bet at m=0.03, p≈0.5 — below the MDE, so even a fully real edge would
pass with only ~60% power; a realistic half-sized edge ~25%. At m=0.02 (n ≈ 3,972, SE ≈ 0.17, MDE ≈ $0.43) the
real edge ≈ $0.4, power ~50%. A NULL therefore means "no edge of ≈ 8% ROI or more", not no edge. Per book (n ≈ 200)
the MDE is ≈ $2: per-book PASSes are not expected.

### Line-move analog (note, not a test)
The historical store holds a second snapshot per line: `t5` alongside `t60`, same game, 33.7k (2023) and 34.8k
(2024) Pinnacle lines present at both, of which 3,128 and 4,002 changed `point`. So a historical analog of
LINE_MOVE_TOWARD_MODEL exists (T-60 to T-5 move in the model's direction, already a secondary in ODDS_HISTORY §3).
It would use the OOF model rows; that is a separate rule, not part of this one.

## Stage B — FORWARD, Kalshi (paper)
Forward only: regular-season games tipping after this rule's freeze commit.

- **Sources**: Pinnacle from `data/markets/market_asof.duckdb`, table **`live_book_odds`** (book = `pinnacle`;
  markets `player_points/rebounds/assists/threes`; the-odds-api live pull, snapshot kinds t180 ... t5, close).
  Correction to the brief: `odds_snapshots` in that file holds only 2026-10-10 h2h/spreads/totals and no Pinnacle
  player props; `live_book_odds` is what the forward scorecard reads. Kalshi from `data/raw/live_<date>/kalshi/<series>_p0/`.
- **Kalshi entry snapshot**: identical to STAR_FLOOR_PAPER (frozen text e112a12, M1-M6 reviewed): per series, the last
  capture file with tipoff − 10 min <= t < tipoff (`forward_predictions.tipoff`, UTC); `event_ticker` date token ==
  the game's America/New_York date AND the player's team is one of the game's two teams (roster cache); status
  `active`; title matches `^(.+?): (\d+)\+ (points|rebounds|assists|threes)$`; bid/ask exact decimals,
  0 < bid < ask < 1 (two-sided), ask − bid <= 0.05; name NFKD/ASCII/lowercase-letters matched to the game's
  `forward_predictions` players, zero or >1 match dropped and counted; each ticker traded at most once.
- **Line mapping**: Kalshi "k+" ≡ Pinnacle over at point k − 0.5, exact match only. Whole-number Pinnacle lines
  are excluded (a snapshot with no k − 0.5 pair is `no_exact_half_line_pair`, counted).
- **Pinnacle snapshot**: per (game, player, market), the latest `received_at` that is <= the Kalshi entry file time
  t and < tipoff, and it must be no more than **N = 15 minutes** older than t, and must hold both an over and an
  under at k − 0.5 in that same `received_at`. Otherwise the line is dropped and counted by reason.
- **De-vig**: two-way proportional, fair `f = ro/(ro+ru)` from the American prices (exact fractions); both sides required.
- **Both directions**: YES when `f − ask >= m`. NO when `(1 − f) − (1 − bid) = bid − f >= m` (price of NO = 1 − yes_bid).
  At most one direction can qualify. **m = 0.03** (exact arithmetic with Fractions/Decimals).
- **Trade**: $10 at the YES ask (or the NO price); contracts = floor(10 / price); fee = ceil_to_cent(coefficient ×
  contracts × price × (1 − price)) once per order, coefficient and multiplier read from `configs/parlay.yaml` at
  freeze (0.07 taker, multiplier 1.0 today). Hold to settlement.
- **Settlement (copy of STAR_FLOOR M5)**: payout = contracts × the per-contract settlement value from Kalshi's
  settled market record: `result` yes = $1.00, no = $0.00, and a fair-price settlement (player inactive or never
  takes the court, or game cancelled, per the market's `rules_secondary`) = the dollar value Kalshi pays. For a NO
  trade the NO contract pays $1 − the YES value. Fair-price trades stay in the primary test and are reported
  separately (count and mean payout). A trade whose settlement record cannot be retrieved within 7 days of the game
  is dropped and counted; if dropped trades exceed 5% of all trades, the result is INVALID and not scored.
  Return per trade = payout − contracts × price − fee.
- **Primary test**: mean return per trade > 0 with the lower bound > 0 of the 95% percentile CI from resampling
  dates with replacement (10,000, `numpy.random.default_rng(20261022)`, dates sorted ascending, trades within a date
  sorted by (game_id, ticker)), AND the t-interval on per-date means (df = dates − 1) lower bound > 0.
  PASS = both. HARM = percentile CI wholly < 0.
- **Horizon**: scored once, on the first slate date D at whose end there are >= 100 settled trades over >= 14
  distinct dates (all of D's trades included), else once on all settled trades at the last regular-season date of
  2026-27. < 30 trades or < 10 dates → INCONCLUSIVE (underpowered, not a null).
- **Reported, not decisive**: realised hit rate vs mean Pinnacle fair vs mean Kalshi price (who is better
  calibrated; for NO trades on the NO side); by stat; by direction; distinct (player, stat, game) beside the trade
  count (S1); per date, games in Kalshi but not in `forward_predictions` and name drops (S2); the same comparison
  with our model's blend as a second "fair value" (stored `p_ge` for the line, model and version pinned at freeze),
  reported only, never traded on.
- **Confirmation status**: a Stage B result counts as confirmation only if Stage A is PASS (see above).

### Stage B gate (label-free; Kalshi captures 2026-10-21 and 2026-10-22 (two games) + `live_book_odds`)
No Kalshi capture exists for 2026-10-20 (`data/raw/live_2026-10-20` was never created; the live capture began 10-21),
so that night contributes 0; Pinnacle has the 10-20 slate (3 games) but nothing to pair.

| item | 10-21 (11 games) | 10-22 (2 games, CLE@PHI + 1) |
|---|---|---|
| Kalshi prop lines, entry-valid (active, two-sided, spread <= 0.05, right NY date, player in game) | 1,296 | 276 |
| dropped for spread > 0.05 | 761 | 143 |
| no Pinnacle snapshot at or before entry | 403 | 57 |
| Pinnacle snapshot without exact k − 0.5 pair | 702 | 182 |
| **lines with a Pinnacle half-line pair (any age)** | **191** | **37** |

Pinnacle snapshot age vs Kalshi entry for the 228 matched lines: median 4.6 min, p90 37.4, max 42.7. Cumulative
counts: age <= 5: 123; <= 10: 151; <= 15: 187; <= 20: 187; <= 30: 187. Choosing **N = 15**: there is a plateau at
15 (nothing between 15 and 30; the remaining 41 lines are 30+ minutes old, i.e. earlier snapshots), so N = 15
loses nothing that is fresh and excludes stale ones. Median Pinnacle overround on these 228 pairs: 6.96%.

Qualifying trades (pre-fee margin), 10-21 / 10-22, N = 15:

| m | 10-21 | 10-22 | total | note |
|---|---|---|---|---|
| 0.02 | 20 | 2 | 22 | fee (about 1.75c at p = 0.5) eats the whole margin |
| **0.03** | 5 | 0 | 5 | |
| 0.04 | 0 | 0 | 0 | |
| 0.05+ | 0 | 0 | 0 | |

(N = 5/10 give 14/16 at m = 0.02 and 2 at 0.03; N = 20/30 equal N = 15.) **Chosen m = 0.03**: the smallest
tested margin that clears the per-contract fee (≤ 1.75c) plus the 5c-spread rounding with room; 0.02 is a coin-flip
after fees (expected net edge ≈ $0.2 per $10 against SD ≈ $11). **The gate result is the headline: 5 trades in
two slates (~2.5 per 11-game night, ~1 on a 7-game night).** 100 trades over 14 dates needs roughly 40-100 nights;
the season has ~165, so the 100/14 horizon is reachable only late in the season, and the horizon falls back to
season end. 14 dates binds only if trades/night >= 7; it does not.

**MDE arithmetic (no returns)**: at m = 0.03 a $10 trade (about 20 contracts at p ≈ 0.5) has SD ≈ $11.0.
At n = 100, SE ≈ $1.10 (clustering ignored, so a lower bound on the MDE) and the 80%-power one-sided MDE is ≈ $3.1
per $10 trade (≈ 31% ROI); at n = 300 it is ≈ $1.8. The best case if the Kalshi price is exactly 3c below a true
fair is ≈ +$0.31 per trade net of fee. Detecting that needs about 10,000 trades (24,000 at m = 0.02). **Stage B
cannot be powered this season for a realistic edge**: it can only detect a gross mispricing near 30% ROI. A PASS
there would be a big surprise (or a quirk); a NULL means almost nothing; INCONCLUSIVE is the likely outcome (< 30
trades) for weeks. This is why Stage A runs first.

## Known limits
- Pinnacle's quote is a snapshot taken at most 15 minutes before Kalshi's, so a stale-looking Kalshi price may only
  be the Kalshi price after the news that moved Pinnacle later; entering at Kalshi's later time with an older
  Pinnacle (N) can look like edge when it is just Pinnacle lag, or hide it. Not separable here.
- Paper fills at the quoted ask/bid; depth is not used. Kalshi's thin ladders cannot absorb $10 repeatedly at scale.
- Stage A uses displayed retail prices with no limits, no account restrictions and no 5-minute execution delay.
- Pinnacle's own de-vig is proportional; a favourite-longshot skew in the vig would bias fair value, and the
  direction of the bias differs for overs and unders (reported by side).

## Open choices for Devin (not decided here)
1. m = 0.03 (5 trades in 2 slates) vs 0.02 (22 trades, expected net edge ≈ 0).
2. N = 15 vs 10 (counts differ by 36 lines but no qualifying trades at m = 0.03 in N 10 → 2).
3. Whether Kalshi-vs-Pinnacle is worth tracking forward at all given the MDE (cheap: the harness is the STAR_FLOOR one).
4. The model-blend second fair value: which stored model/version to pin.
5. BH family of 7 retail books vs adding novig/prophetx (exchanges, 2024 only).
