# STAR_FLOOR_PAPER — does Kalshi underprice lines a same-team player has historically almost always hit? (paper only)

Status: DRAFT (freeze pending adversary review; must freeze before the DEN@OKC pre-tip window, 2026-10-23 01:20Z).
Forward only, paper only (non-negotiable 5). Devin's idea (2026-10-22: "LeBron always scores 10; look at how often it
doesn't happen"); thresholds chosen by Claude from label-free line counts on the 2026-10-21 capture (no outcome read).

## Rule
- **Universe**: Kalshi player-prop markets `KXNBAPTS`, `KXNBAREB`, `KXNBAAST`, `KXNBA3PT` ("<Name>: <k>+ <stat>") for
  regular-season games whose tipoff is after this rule's freeze commit (2026-10-22 CLE@PHI is excluded: it tipped first).
- **Entry snapshot**: the last live-capture snapshot strictly before tipoff (`forward_predictions.tipoff`, UTC) and no
  more than 10 minutes before it; the line must be two-sided (0 < bid < ask < 1) with ask − bid <= 0.05.
- **Same team**: the player is on the game's official roster cache for the slate date AND his team_id in his last
  regular-season game before 2026-10-01 (season 2025; read logged in HOLDOUT_ACCESS_LOG as eligibility only) is the same
  team. Players with no such game are ineligible.
- **History**: regular-season games with minutes > 0 in seasons 2022-2024 (`player_game_stats` joined to `games`);
  at least 60 games. hit_rate = share of those games with stat >= k.
- **Eligible**: hit_rate >= 0.90 AND ask <= hit_rate − 0.05. One paper trade per eligible line (a player may have
  several lines; all count, and the report also gives one-line-per-player-stat results).
- **Entry**: $10 at the ask; contracts = floor(10 / ask); fee = ceil_to_cent(0.07 × contracts × ask × (1 − ask)) once
  per order (configs/parlay.yaml values at freeze). **Exit**: hold to settlement ($1 per contract on Yes). A market
  voided by Kalshi is dropped and counted; a player who does not play settles as Kalshi settles it, reported.
- **Return** per trade = payout − cost − fee.

## Primary test
Mean return per trade > 0 with the lower bound > 0 of the 95% percentile CI from resampling dates with replacement
(10,000 resamples, numpy.random.default_rng(20261022); dates sorted ascending, trades within a date sorted by
(game_id, ticker)); the statistic is the pooled mean over trades in the resampled dates. Also required: the
t-interval on per-date means (df = dates − 1) has a lower bound > 0. PASS = both. HARM = percentile CI wholly below 0.

## Horizon (fixed)
Scored once on the first date with >= 100 trades AND >= 14 distinct dates (expected ~17 slate nights at ~6/night).

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
