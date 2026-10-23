# STAR_FLOOR_PAPER — does Kalshi underprice lines a same-team player has historically almost always hit? (paper only)

Status: FROZEN (this commit; text sha256 in DECISIONS). Adversary pre-freeze review docs/reviews/prefreeze_star_floor_2026-10-22.md: MUSTs M1-M6 adopted verbatim.
A NULL means no edge of about +10% ROI or more (MDE ~ $1 per $10 trade), not no edge.
Forward only, paper only (non-negotiable 5). Devin's idea (2026-10-22: "LeBron always scores 10; look at how often it
doesn't happen"); thresholds chosen by Claude from label-free line counts on the 2026-10-21 capture (no outcome read).

## Rule
- **Universe**: Kalshi player-prop markets `KXNBAPTS`, `KXNBAREB`, `KXNBAAST`, `KXNBA3PT` ("<Name>: <k>+ <stat>") for
  regular-season games whose tipoff is after this rule's freeze commit (2026-10-22 CLE@PHI is excluded: it tipped first).
- **Entry snapshot**: for each series separately, the last file in
  `data/raw/live_<slate date>/kalshi/<series>_p0/` whose filename UTC timestamp t satisfies
  tipoff − 10 min <= t < tipoff (`forward_predictions.tipoff`, UTC). A market belongs to the game only if the date
  token of its `event_ticker` (YYMONDD, e.g. `26OCT22`) equals the game's America/New_York date AND the player's team
  is one of the game's two teams. Markets with any other date are ignored. The market must have `status` = `active`
  and a title matching `^(.+?): (\d+)\+ (points|rebounds|assists|threes)$`. Bid and ask are parsed as exact decimals
  from `yes_bid_dollars` / `yes_ask_dollars`, with 0 < bid < ask < 1 and ask − bid <= 0.05. Player identity: the
  title name is normalised (NFKD, ASCII, lowercase letters only) and matched to the game's `forward_predictions`
  players. Zero matches or more than one match drops the line, and the drop is counted. Each ticker is traded at most
  once.
- **Same team**: the player is on the official roster cache for the slate date for one of the game's two teams, AND
  his `team_id` in his latest (`max(game_date)`) season-2025 game with `game_id LIKE '002%'` and
  `game_date < 2026-10-01` is that same team. Players with no such game are ineligible. From season 2025 the harness
  reads only `player_id`, `team_id`, `game_id` and `game_date`; it reads no stat, minutes or score column. These
  nightly reads are covered by the 2026-10-22 HOLDOUT_ACCESS_LOG row for this rule, extended to say so before the
  harness first runs.
- **History**: games with `game_id LIKE '002%'` (regular season; excludes play-in `005`, playoffs `004` and any other
  prefix) and `games.season` in (2022, 2023, 2024), joined to `player_game_stats` with `minutes > 0` (NULL minutes
  excluded); at least 60 such games. With n such games and h of them with stat >= k, hit_rate = h / n.
- **Eligible**: with n, h from History and ask a as an exact decimal: 10·h >= 9·n AND 100·a·n <= 100·h − 5·n
  (i.e. hit_rate >= 0.90 and a <= hit_rate − 0.05, evaluated exactly, without floating point). One paper trade per eligible line (a player may have
  several lines; all count, and the report also gives one-line-per-player-stat results).
- **Entry**: $10 at the ask; contracts = floor(10 / a); fee = ceil_to_cent(0.07 × contracts × a × (1 − a)), once per
  order (fee multiplier 1.0; configs/parlay.yaml at freeze). **Exit**: hold to settlement. payout = contracts × the
  per-contract settlement value from Kalshi's settled market record: `result` yes = $1.00, no = $0.00, and a
  fair-price settlement (player inactive or never takes the court, or game cancelled, per the market's
  `rules_secondary`) = the dollar value Kalshi pays. Fair-price trades stay in the primary test and are also reported
  separately (count and mean payout). A trade whose settlement record cannot be retrieved within 7 days of the game is
  dropped and counted. If dropped trades exceed 5% of all trades, the result is INVALID and not scored.
- **Return** per trade = payout − contracts × a − fee.

## Primary test
Mean return per trade > 0 with the lower bound > 0 of the 95% percentile CI from resampling dates with replacement
(10,000 resamples, numpy.random.default_rng(20261022); dates sorted ascending, trades within a date sorted by
(game_id, ticker)); the statistic is the pooled mean over trades in the resampled dates. Also required: the
t-interval on per-date means (df = dates − 1) has a lower bound > 0. PASS = both. HARM = percentile CI wholly below 0.

## Horizon (fixed)
Scored once, on the first slate date D at whose end there are >= 100 settled trades over >= 14 distinct dates (all
of D's trades included). If that is not reached by the last regular-season date of 2026-27, score once on all
settled trades then. Fewer than 30 trades or fewer than 10 dates → INCONCLUSIVE (underpowered, not a null);
otherwise apply the primary test. Outcomes: PASS (both bounds > 0), HARM (percentile CI wholly < 0), NULL
(otherwise).

## Reported, not decisive
Realised hit rate vs mean historical hit_rate vs mean ask (is history or the market better calibrated?); by stat; by
hit_rate band (0.90-0.95, >= 0.95); DNP count; one-line-per-player-stat version; the same return for the lines with
hit_rate >= 0.90 that did NOT qualify on price (ask > rate − 0.05) as a comparison.

## Minimum-data gate (non-negotiable 8), label-free, 2026-10-21 capture
2,057 pre-tip prop lines matched; 811 same-team, >= 60 games, spread <= 5c; eligible at hit >= 0.90 & ask <= rate − 0.05:
6 (hit >= 0.95 & gap 8c: 0; hit >= 0.85 & gap 5c: 13). ~6/night -> 100 trades in ~17 nights; 14 dates binds earlier.
PASS. Script: research/scratch/star_floor_gate_2026_10_21.py.

## Known limits
- History ends with 2024-25; aging and role drift since then are not captured (the same-team filter removes the
  largest role changes, not all of them). This is exactly what the test measures against the market.
- Paper fills at the quoted ask; book depth is captured and reported, not used.
- The harness is written after this freeze and checked against this text before scoring.
