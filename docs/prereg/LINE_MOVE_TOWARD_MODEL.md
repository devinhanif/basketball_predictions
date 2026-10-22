# LINE_MOVE_TOWARD_MODEL — does the Kalshi prop price move toward our forecast before tip?

Status: FROZEN (this commit; text sha256 in DECISIONS). Forward only. Drafted 2026-10-22 by the main session at
Devin's request ("yes, do that"); adversary pre-freeze review docs/reviews/prefreeze_line_move_fav_dip_2026-10-22.md
(MUSTs L1-L5 adopted verbatim; SHOULDs L-S1..L-S7 adopted as noted). First data date 2026-10-22.

## Question
When the production prop model disagrees with the Kalshi price about 2 hours before tip, does the price
move toward the model by tip-off, and more than it moves toward the recency baseline from the same run, which
shares M's p_play (injury-report availability) but not its context residual? This is the gate for raising the
model's weight in the blend above the paper trader's 0.25.

## Data (already captured write-once)
- Prices: `data/raw/live_<date>/kalshi/{KXNBAPTS,KXNBAREB,KXNBAAST,KXNBA3PT}_p0/*.json.gz` (the live capture).
- Forecasts: `forward_predictions`. M = the latest `props_context_residual` row per (game_id, player_id,
  target) with `made_at <= tipoff − 60 min`; B = the `props_recency_v1` row with the same `run_id`, game_id,
  player_id and target (dropped and counted if absent). `tipoff` is the scheduled tip stored in
  `forward_predictions`, read as UTC. A snapshot's time is the UTC timestamp in its capture file name. A
  market's date is its tipoff converted to an America/New_York calendar date.
- Player match: the Kalshi title "<Name>: <k>+ <stat>", matched by `configs/kalshi_aliases.yaml` first, then
  exact normalized full name; unmatched markets are dropped and counted.
- Kalshi resolution when the player does not play: p_M treats a DNP as "No"; the harness states Kalshi's
  actual rule from the market's rules text and reports any market that resolved otherwise.

## Definitions
- **S0 (sign snapshot)**: the first capture snapshot at or after max(made_at of M, tipoff − 150 min).
  **S1 (start snapshot)**: the first capture snapshot at or after S0 + 15 min. **close snapshot**: the last
  capture snapshot strictly before tipoff. S0, S1 and close must each be two-sided (0 < bid < ask < 1) with
  ask − bid <= 0.10, and close − S1 >= 30 min; otherwise the market is dropped and counted.
- **mid0, mid1, mid_close** = (bid + ask) / 2 at S0, S1 and close.
- **p_M(k)** = M's unconditional P(stat >= k) = `p_ge_full` c[k−1]/n (0 when k−1 >= len(c)) × `p_play`.
  **p_B(k)** the same for B. A null `p_ge_full` drops the market (counted).
- **One market per player-stat-game**: among the rungs k whose S0 quote passes the S0 filter, the rung whose
  mid0 is closest to 0.50 (ties: lower k). If that rung then fails the S1 or close filter, the player-stat-game
  is dropped, with no fallback to another rung, and counted separately by sign(p_M − mid0).
- **Qualifying (part 1)**: |p_M − mid0| >= 0.05. **Qualifying (part 2)**: |p_M − mid0| >= 0.05 OR
  |p_B − mid0| >= 0.05.
- **Movement toward X** (X in {M, B}): m_X = (mid_close − mid1) × sign(p_X − mid0), in probability units;
  sign(0) = 0.

## Primary test (one family, two parts, both must pass)
CI method (both parts): the 95% percentile CI from resampling dates with replacement (10,000 resamples,
`numpy.random.default_rng(20261022)`; dates sorted ascending, markets within a date sorted by (game_id, ticker)
before resampling); the statistic on each resample is the pooled mean over every market in the resampled dates
(each market weighted equally); part 2 resamples the per-market difference m_M − m_B the same way.
Also required (L-S3 adopted): the t-interval on the per-date means (df = number of dates − 1) has a lower bound > 0.
1. **Part 1**: over part-1 qualifying markets, mean m_M >= 0.010 and both lower bounds > 0.
2. **Part 2**: over part-2 qualifying markets (selected symmetrically in M and B), mean (m_M − m_B) >= 0.005 and
   both lower bounds > 0.
PASS = both parts. HARM = part 1's percentile CI wholly below 0. Else NULL.

## Horizon (fixed; no peeking-stop)
Scored once, on the first date by which the window holds at least **300 part-1 qualifying markets AND 14 distinct
dates**, starting 2026-10-22 (inclusive). The window is not extended or cut for any other reason. A date whose
capture failed is absent (logged), not imputed.

## Harness requirements before the scoring date
Written after this freeze; must implement this text exactly; adversary checks it against this text. Before it reads
real mids it passes a synthetic dryrun (L-S2): (i) pure reverting quote noise with p_M and p_B independent of it,
under the observed spread distribution and stale-quote persistence: part 2 mean ~0 and part-1 false-pass rate
<= 5% over 1,000 draws; (ii) a planted +0.02 move toward M passes. If (i) fails, the rule is reported as
unscoreable, not run.

## Reported, not part of the decision
Per stat; by gap size (0.05–0.10, > 0.10); moved toward / away / flat counts; a sign-shuffled placebo; drops by
reason, by sign(p_M − mid0) and by M's p_play < 0.9 (late scratches, L-S6). 2026-10-21 is EXCLUDED (gate night).

## What it decides
- PASS: propose (Devin decides) raising the model weight in the paper trader blend from 0.25, under a new frozen
  rule that picks the new weight on later data.
- NULL / HARM: the weight stays at or below 0.25; "the market knows more" stands. One follow-up at most.

## Minimum-data gate (non-negotiable 8), label-free, on the 2026-10-21 capture
1,312 prop forecasts; 2,129 Kalshi prop markets matched; 1,507 with valid quotes at two snapshots >= 30 min apart;
775 with |p_M − mid| >= 0.05 before the one-rung rule; 339 after it; 324 under this text (S0/S1 decoupled,
no rung fallback; 136 with M and B on opposite sides). 300 is reachable in about one night; 14 dates binds
(about 2026-11-04 at the earliest). PASS. Script: research/scratch/gate_line_move_fav_dip_2026_10_21.py.

## Known limits
- M is the morning run (made_at ~8–11 h before tip; no later refresh); the test is whether a morning forecast
  predicts the last ~2 h of movement, where confirmed lineups land.
- Decoupling S0 from S1 does not remove noise that persists from S0 through S1 (stale quotes); the dryrun measures it.
- No movement statistic on any date from 2026-10-22 is computed or read before this rule's freeze commit.
  Any descriptive comparison (stated in UTC) runs only after that commit and can change nothing here.
