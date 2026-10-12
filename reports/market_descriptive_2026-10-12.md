# Market descriptive checks, 2026-10-12 (DESCRIPTIVE: no rule, no claim, no verdict)

Approved by Devin 2026-10-12 ("lets do everything"). Seasons 2023 (2023-24) and 2024 (2024-25) only;
season 2025 is never loaded (`model_clv_eval.load_season` refuses it). Nothing here is a bet
recommendation: every cell below loses money at the stored price except a few small subsets picked
out of about 80, which is what chance looks like at that count.

## Task 1: model over/under picks against stored T-60 lines

Data: the MODEL_CLV paired frames (`data/ops/model_clv_pairs/pairs_{2023,2024}.parquet`, built by
`research/eval/model_clv_pairs.py` with the ODDS_HISTORY join). These are the production props model's
P(over) and the book's T-60 quote for the same player, stat and line. Books: Pinnacle and DraftKings.
All lines are x.5, so there are 0 pushes (checked in code). DNP legs are voided and excluded upstream
(per-cell counts are in `reports/odds_history.md`). Model pick: over if P_model(over) > 0.5. Market
pick: over if the multiplicative de-vigged P(over) > 0.5. Break-even (be) is the mean vigged implied
probability of the picked side at the stored T-60 price. ret is the realised return per $1 at that
price. CIs: 95%, games resampled (2,000 draws). Script: `research/eval/market_ou_descriptive.py`.
Full results, including each season separately: `reports/market_descriptive_2026-10-12.json`.

**52.4% is not the bar.** That figure is the break-even at -110. The prices actually stored put the
break-even at 54-61%, and every pooled hit rate below sits under its own break-even.

### Hit rate by pick side, pooled 2023+2024, T-60

| book | stat | n legs / games | model OVER hit (n) | be | ret | model UNDER hit (n) | be | ret | market OVER hit (n) | market UNDER hit (n) |
|---|---|---|---|---|---|---|---|---|---|---|
| Pinnacle | pts | 25,210 / 2,506 | 50.7 [49.6, 51.8] (7,238) | 54.3 | -6.6% [-8.6, -4.6] | 52.4 [51.7, 53.2] (17,972) | 54.2 | -3.1% [-4.4, -1.7] | 50.9 [50.0, 51.8] (11,602) | 53.6 [52.8, 54.4] (13,608) |
| Pinnacle | reb | 12,984 / 2,505 | 54.6 [52.8, 56.5] (2,700) | 56.3 | -2.7% [-6.0, +0.8] | 55.3 [54.3, 56.3] (10,284) | 55.4 | -0.2% [-2.1, +1.5] | 50.9 [49.6, 52.2] (5,374) | 56.2 [55.1, 57.3] (7,610) |
| Pinnacle | ast | 13,485 / 2,502 | 54.1 [52.5, 55.6] (3,722) | 56.9 | -5.0% [-7.8, -2.2] | 53.7 [52.8, 54.7] (9,763) | 56.1 | -4.1% [-5.8, -2.3] | 54.1 [52.9, 55.3] (5,874) | 55.9 [54.8, 57.1] (7,611) |
| Pinnacle | fg3m | 20,995 / 2,493 | 56.9 [55.4, 58.3] (4,159) | 61.0 | -6.6% [-9.0, -4.2] | 56.3 [55.5, 57.1] (16,836) | 58.0 | -2.8% [-4.1, -1.4] | 54.4 [53.3, 55.4] (8,476) | 59.1 [58.3, 60.0] (12,519) |
| Pinnacle | all | 72,674 / 2,509 | 53.4 [52.7, 54.2] (17,819) | 56.7 | -5.7% [-7.1, -4.3] | 54.4 [53.9, 54.9] (54,855) | 55.9 | -2.6% [-3.5, -1.7] | 52.4 [51.8, 53.0] (31,326) | 56.2 [55.6, 56.7] (41,348) |
| DK | pts | 30,236 / 2,616 | 50.5 [49.5, 51.4] (9,254) | 53.9 | -6.3% [-8.2, -4.6] | 52.3 [51.7, 52.9] (20,982) | 53.6 | -2.4% [-3.6, -1.2] | 51.5 [50.6, 52.3] (12,663) | 53.6 [52.9, 54.3] (17,573) |
| DK | reb | 29,800 / 2,613 | 53.8 [52.8, 54.8] (8,885) | 56.1 | -4.0% [-5.9, -2.3] | 55.4 [54.8, 56.1] (20,915) | 55.0 | +0.9% [-0.3, +2.1] | 52.2 [51.3, 53.0] (13,056) | 56.4 [55.7, 57.2] (16,744) |
| DK | ast | 20,224 / 2,608 | 54.1 [52.9, 55.3] (5,999) | 56.6 | -4.2% [-6.3, -2.1] | 54.1 [53.3, 54.9] (14,225) | 55.6 | -2.7% [-4.0, -1.2] | 53.6 [52.6, 54.6] (8,720) | 55.7 [54.8, 56.6] (11,504) |
| DK | fg3m | 26,739 / 2,612 | 58.6 [57.5, 59.8] (7,175) | 60.6 | -3.2% [-5.1, -1.3] | 56.8 [56.1, 57.5] (19,564) | 57.6 | -1.2% [-2.4, +0.1] | 56.0 [55.1, 56.9] (11,654) | 59.4 [58.7, 60.2] (15,085) |
| DK | all | 106,999 / 2,616 | 54.0 [53.4, 54.6] (31,313) | 56.6 | -4.6% [-5.6, -3.5] | 54.7 [54.3, 55.1] (75,686) | 55.4 | -1.2% [-2.0, -0.5] | 53.2 [52.7, 53.7] (46,093) | 56.2 [55.8, 56.7] (60,906) |

UNDER minus OVER hit rate (all stats), percentage points: model, Pinnacle +1.0 [0.0, +1.9], DK +0.7
[-0.0, +1.4]; market, Pinnacle +3.7 [+2.9, +4.7], DK +3.0 [+2.3, +3.7]. Each season separately points
the same way (JSON).

### By model confidence |P - 0.5|, all stats pooled

| book | bucket | OVER hit (n) | be | ret | UNDER hit (n) | be | ret |
|---|---|---|---|---|---|---|---|
| Pinnacle | 0-0.05 | 52.3 [51.2, 53.4] (9,154) | 55.7 | -6.1% [-7.9, -4.1] | 51.0 [50.1, 51.9] (12,917) | 52.9 | -3.5% [-5.2, -1.8] |
| Pinnacle | 0.05-0.10 | 53.5 [52.1, 54.9] (4,972) | 56.9 | -5.8% [-8.4, -3.3] | 52.8 [52.0, 53.7] (14,474) | 54.7 | -3.2% [-4.7, -1.6] |
| Pinnacle | 0.10-0.20 | 55.7 [54.0, 57.5] (3,092) | 58.5 | -4.7% [-7.8, -1.6] | 55.9 [55.2, 56.6] (20,969) | 57.4 | -2.5% [-3.7, -1.2] |
| Pinnacle | >0.20 | 58.9 [54.9, 62.8] (601) | 61.2 | -4.0% [-10.6, +2.4] | 59.7 [58.4, 61.0] (6,495) | 59.9 | -0.1% [-2.3, +2.1] |
| DK | 0-0.05 | 52.0 [51.2, 52.9] (15,045) | 55.2 | -5.7% [-7.2, -4.1] | 50.9 [50.2, 51.6] (19,633) | 52.9 | -3.6% [-5.0, -2.2] |
| DK | 0.05-0.10 | 53.8 [52.7, 54.8] (8,987) | 56.7 | -5.1% [-7.0, -3.3] | 53.6 [52.9, 54.3] (20,878) | 54.6 | -1.7% [-2.9, -0.4] |
| DK | 0.10-0.20 | 57.5 [56.2, 58.7] (6,088) | 58.7 | -2.0% [-4.2, +0.1] | 56.4 [55.8, 57.1] (27,392) | 56.8 | -0.4% [-1.5, +0.7] |
| DK | >0.20 | 62.4 [59.6, 65.1] (1,193) | 61.7 | +0.8% [-3.8, +5.3] | 60.8 [59.6, 61.9] (7,783) | 59.0 | +3.2% [+1.2, +5.2] |

Per-stat buckets are in the JSON. The one cell that keeps showing up is **reb UNDER at >0.20**: DK
+8.4% [+4.0, +12.8] (n 1,656) and Pinnacle +6.9% [+2.1, +11.7] (n 1,156) pooled, positive in both
seasons on both books. That is the same stat where ODDS_HISTORY's blend beat Pinnacle. It is one of
about 80 uncorrected subset cells, chosen after looking, so it is not a claim. If Devin wants it
tested, it needs a new pre-registered rule scored on the live forward log, never on 2025.

### Calibration at the line: mean P(over) minus realised over rate, pooled, percentage points

| book | stat | role | n | realised over | model mean | model gap | market mean | market gap |
|---|---|---|---|---|---|---|---|---|
| Pinnacle | all | all | 72,674 | 47.5 [47.1, 48.0] | 43.3 | -4.3 [-4.7, -3.8] | 49.0 | +1.5 [+1.1, +1.9] |
| Pinnacle | all | starter | 57,257 | 47.7 [47.2, 48.2] | 43.8 | -3.8 [-4.3, -3.3] | 49.2 | +1.5 [+1.0, +2.1] |
| Pinnacle | all | bench | 15,417 | 47.0 [46.1, 47.9] | 41.1 | -5.9 [-6.8, -4.9] | 48.4 | +1.4 [+0.5, +2.3] |
| Pinnacle | pts | all | 25,210 | 48.5 | 44.8 | -3.6 [-4.2, -3.0] | 49.8 | +1.3 [+0.7, +1.9] |
| Pinnacle | reb | all | 12,984 | 46.7 | 42.3 | -4.4 [-5.4, -3.6] | 49.1 | +2.3 [+1.4, +3.2] |
| Pinnacle | ast | all | 13,485 | 48.4 | 44.4 | -4.0 [-4.8, -3.2] | 49.2 | +0.7 [-0.1, +1.5] |
| Pinnacle | fg3m | all | 20,995 | 46.3 | 41.2 | -5.1 [-5.8, -4.4] | 48.0 | +1.7 [+1.1, +2.4] |
| DK | all | all | 106,999 | 47.9 [47.5, 48.2] | 44.6 | -3.3 [-3.6, -2.9] | 49.5 | +1.6 [+1.3, +2.0] |
| DK | all | starter | 79,047 | 48.1 [47.6, 48.5] | 45.0 | -3.1 [-3.5, -2.6] | 49.6 | +1.5 [+1.1, +2.0] |
| DK | all | bench | 27,952 | 47.3 [46.6, 48.0] | 43.5 | -3.8 [-4.5, -3.1] | 49.1 | +1.8 [+1.2, +2.5] |

Starter = starter10 >= 0.5. Every stat, both books, both seasons and both roles are in the JSON, and
all of them carry the same signs.

**Reading.** The two sides are off in opposite directions. The model puts P(over) at the line about
4 points too low, and the gap is larger for bench players on Pinnacle. Its mean is about right (model
mean minus outcome is -0.03 to -0.20 per stat), so the error is in the shape of the distribution: too
much probability just below the line. As a result the model picks UNDER on 75% of legs. The market's
multiplicative de-vig puts P(over) about 1.5 points too high, which is the usual shading of the over.
That shading is where the under asymmetry comes from. The market's own under picks beat its over
picks by 3-4 points. The model's under picks beat its over picks by only about 1 point, because the
model's low bias pushes it into the low-confidence unders (51% hit rate, below break-even).

Link to T474: T474 found the starters' far lower tail too thin (more outcomes fall below the model's
q10 than 10%). Here, near the median, starters have too much mass below the line. These two findings
do not conflict. Both say the left half of the starters' distribution has the wrong shape, not the
wrong location. So "unders are systematically better" is true of the market's prices, not of our
model.

## Task 2: Kalshi vs Pinnacle on player props (live captures)

**Plain answer: this cannot be measured yet. No Pinnacle data of any kind (props or game lines) has
been captured for any 2026 game.** Kalshi does list NBA player props, but sparsely.

What exists (copies of both stores were read, read-only; summary in
`reports/market_descriptive_2026-10-12_kalshi.json`):

- **Kalshi.** The collector requests 10 prop series every 15 minutes. In the 2026-27 preseason, only
  KXNBAPTS (points) has listed anything: 3 events (NYK-PHI on 10-05, BOS-CLE and PHI-BKN on 10-08), 8
  markets each, 24 in total. All are ladder markets ("Player: N+ points", N = 10/15/20/25), and all 24
  are matched to a player_id. Prices exist only for the two 10-08 games: 16 tickers, 29 snapshots, 340
  rows between 19:47 and 02:59 UTC. Pre-tip spreads are huge (median yes_ask - yes_bid 0.48-0.62 per
  game, 0.42 overall, p10 0.03, p90 0.85). Buying both sides at the ask, fee included, costs a median
  $1.45 for a $1 payout. The highest volume seen was 3,186 contracts. Since 10-09, only the GAME, SPREAD
  and TOTAL series have open markets. (The table also holds 2026 Finals prop markets, which are season
  2025. Their metadata appeared in an inventory listing; no prices were loaded and nothing was scored.)
- **Pinnacle.** `data/markets/market_asof.duckdb` has `live_book_odds` = **0 rows**. `odds_snapshots`
  holds only the cancelled vendor's single 10-10 pull: game markets, no Pinnacle, no props.
  `live_capture_log` shows the only attempts, for preseason games 0012600040/41 on 10-11, every one
  `unmatched: no unique vendor event`. The capture is on (`NBA_CLOSE_CAPTURE=1` in `.env`).

Why the Pinnacle side is empty:
1. `nba/odds/live_close.py` filters out `001` preseason game ids (`is_scorable_game_id`; commit
   f014b9c, 10-11 23:06 CT). The 10-11 attempts ran before that commit.
2. The capture queries `sport_key: basketball_nba` (`configs/odds_history.yaml`). Preseason events
   are listed under `basketball_nba_preseason`, so the match fails either way.

Both coordinator add-ons are not feasible for the same reason:
- (a) Lag after news (injury-report `as_of` changes and lineup snapshots; gap just before and at 15,
  30 and 60 minutes after): there is no Pinnacle price series, for props or for the game line, to
  compare against. n = 0 in every cell.
- (b) Open vs close (first Kalshi snapshot of the day and the 10:00 ET Pinnacle `morning` capture,
  compared with close): no Pinnacle morning or close capture exists for 2026. n = 0.

Even with a Pinnacle side, the preseason Kalshi sample (2 priced games, 16 ladders, pts only, spreads
around 0.5) could answer whether the comparison is feasible and nothing more.

Proposed minimal read-only fixes (**not made**; production and money decisions belong to Devin):
- Do nothing for the preseason (it ends this week). The regular-season capture (12 points per game,
  T-180 to close plus a 10:00 ET morning snapshot, region `eu` = Pinnacle) produces the Pinnacle side
  automatically from opening night.
- Kalshi lists "N+" ladders, and those match only Pinnacle lines at N - 0.5. Keep the
  `player_alternate` group enabled (it is now, at 9.2% of the plan). If budget trimming ever drops
  `alternate: true` groups, nearly all prop matches are lost.
- Optional, if Devin wants a preseason look before opening night: allow `001` games in the live
  capture with `sport_key: basketball_nba_preseason`. That is a production change and costs credits.
  I recommend against it, because preseason Kalshi prop liquidity is negligible.
- Matching rule for the live comparison: Kalshi snapshot within +/-7.5 minutes of the odds capture
  tick (the Kalshi cadence is 15 minutes); Kalshi mid compared with Pinnacle's multiplicative no-vig
  P(over N - 0.5); "gap exceeds break-even" computed with `nba.parlay.ev.fee_per_contract` on the
  verified `configs/parlay.yaml` fee model (taker 0.07 x P x (1 - P), rounded up per order).
