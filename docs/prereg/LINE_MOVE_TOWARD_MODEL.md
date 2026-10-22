# LINE_MOVE_TOWARD_MODEL — does the Kalshi prop price move toward our forecast before tip?

Status: DRAFT (freeze pending adversary pre-freeze review; must freeze before 21:00Z 2026-10-22). Forward only.
Drafted 2026-10-22 by the main session at Devin's request ("yes, do that"); first data night 2026-10-22.

## Question
When the production prop model disagrees with the Kalshi price about 2 hours before tip, does the price
move toward the model by tip-off more than it moves toward an information-matched baseline? This is the
fastest honest test of whether the model knows something the market has not priced yet. It is the gate
for raising the model's weight in the blend above the paper trader's 0.25.

## Data (all already captured write-once; nothing new to build before tonight)
- Prices: `data/raw/live_<date>/kalshi/{KXNBAPTS,KXNBAREB,KXNBAAST,KXNBA3PT}_p0/*.json.gz` (the live capture).
- Forecasts: `forward_predictions`, model `props_context_residual` (M) and `props_recency_v1` (B, the
  baseline), the latest run per (game, player, stat) with `made_at <= ref time` and `made_at <= tip - 60 min`.
- Tip-off: `forward_predictions.tipoff` (real tip time).
- Player match: the Kalshi title "<Name>: <k>+ <stat>", matched by `configs/kalshi_aliases.yaml` first, then
  exact normalized full name; unmatched markets are dropped and counted.

## Definitions
- **ref snapshot**: the first capture snapshot at or after `max(made_at of M, tip - 150 min)`.
  **close snapshot**: the last capture snapshot strictly before tip. Both must be two-sided
  (0 < bid < ask < 1) with ask - bid <= 0.10, and close - ref >= 30 min. Otherwise the market is dropped.
- **mid** = (bid + ask) / 2.
- **p_M(k)** = M's unconditional P(stat >= k) = `p_ge_full` count share x `p_play`. **p_B(k)** the same for B.
- **One market per player-stat-game**: among the rungs k passing the quote filters, the rung whose ref mid is
  closest to 0.50 (ties: lower k). This prevents one player's ladder from counting several times.
- **Qualifying**: |p_M - mid_ref| >= 0.05.
- **Movement toward X** (X in {M, B}): `m_X = (mid_close - mid_ref) * sign(p_X - mid_ref)`, in probability units.
  When p_X equals mid_ref exactly, m_X = 0.

## Primary test (one family, two parts, both must pass)
Over all qualifying markets on all dates in the window:
1. **mean m_M > 0** with the 95% date-clustered bootstrap CI (10,000 resamples, seed 20261022) lower bound > 0
   and mean m_M >= 0.010 (one cent: the practical floor).
2. **mean (m_M - m_B) > 0** with the same CI lower bound > 0, on the same qualifying markets (selected by M's
   gap, so the selection cannot favour B).
PASS = both. Anything else = NULL (or HARM when the CI of part 1 lies wholly below 0).

## Horizon (fixed; no peeking-stop)
Scored once, on the first date by which the window holds at least **300 qualifying markets AND 14 distinct
dates**, starting 2026-10-22 (inclusive). The window is not extended or cut for any reason other than these
counts. If the capture fails for a date, that date is absent (logged), not imputed.

## Reported, not part of the decision
Per stat; by gap size (0.05–0.10, > 0.10); market moved toward / away / flat counts; a sign-shuffled placebo
(should be ~0); a 2026-10-21 replay is EXCLUDED (that night was used to check this gate).

## What it decides
- PASS: propose (Devin decides) raising the model weight in the paper trader blend from 0.25, under a new
  frozen rule that picks the new weight on later data.
- NULL / HARM: the weight stays at or below 0.25; "the market knows more" stands. One follow-up at most.

## Minimum-data gate (non-negotiable 8), checked 2026-10-22 on the 2026-10-21 capture, label-free
1,312 prop forecasts; 2,129 Kalshi prop markets matched to a forecast; 1,507 with two-sided quotes (spread
<= 0.10) at ref and close >= 30 min apart; 775 with |p_M - mid| >= 0.05 before the one-rung-per-player rule
(expected ~100–200 per full slate after it). 300 qualifying markets are reachable in a few nights; the binding
count is 14 dates (about 2026-11-05 at the earliest). PASS.

## Known limits
- The capture window opens about 2 h before the first tip, so the reference price already holds morning
  news; this measures movement in the last ~2 h, where confirmed lineups land.
- Kalshi prop markets are thin; the spread filter and mid prices limit, but do not remove, quote noise.
  The B comparison is the guard: noise moves toward M and B alike.
- The harness is written after this freeze and must implement this text exactly; the adversary checks the
  harness against this text before the scoring date. No movement statistic is computed before then except the
  descriptive 11:30 vs 17:30 comparison for 2026-10-22, which uses neither the ref nor the close snapshot.
