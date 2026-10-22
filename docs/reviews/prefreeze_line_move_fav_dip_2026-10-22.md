# Pre-freeze review: LINE_MOVE_TOWARD_MODEL and FAV_DIP_PAPER (2026-10-22)

Reviewer: adversary. Drafts reviewed at commit cd8fc93 (`docs/prereg/LINE_MOVE_TOWARD_MODEL.md`,
`docs/prereg/FAV_DIP_PAPER.md`); gate script `research/scratch/gate_line_move_fav_dip_2026_10_21.py`.
Freeze deadline: before the capture's first snapshot at 21:00Z 2026-10-22 (heartbeat `window_start`).
No movement or return statistic was computed on any date. Everything below is counts, forecast
fields and quote-filter passes on the 2026-10-21 capture (season 2026, not the 2025 holdout).

Verdict: BLOCKED

| Rule | Verdict |
|---|---|
| LINE_MOVE_TOWARD_MODEL | **NOT_READY**: 5 MUSTs, all text-only and applicable before 21:00Z |
| FAV_DIP_PAPER | **NOT_READY**: 5 MUSTs, all text-only and applicable before 21:00Z |

Both become READY_TO_FREEZE once the replacement texts below go in unchanged. None of them needs new
data or code before tonight. The re-run gate still passes with them (see the gate section).

Top finding: in LINE_MOVE, part 2 (M − B) does not guard against mean reversion. Selecting markets
on M's gap at the same snapshot that both sets the sign and starts the movement biases m_M upward more
than m_B, so part 2 can pass on quote noise alone.

## Playbook check

Model classes: (a) a market-movement test of the props model (`props_context_residual`, a residual
quantile model over the recency distribution) against the props recency baseline; (b) a paper trading
rule on game-winner prices. Neither rule fits a model.

1. **Information or capacity?** (a) tests information: does the context residual carry something the
   market prices later. That is the right question. (b) is neither: it is a price-pattern rule found
   by searching about 40 rules on one night, so the prior that it is real is low.
2. **Enough data for this class?** (a) 339 qualifying markets on one 11-game night, so 300 per night
   is reachable and 14 dates is the binding count. Part 2's information sits only in markets where
   M and B disagree on direction (139 of 339 = 41%; see below). (b) 100 trades at about 5 per night,
   with a minimum detectable effect of about +$2.5 per $10 (power note). It can only detect a large edge.
3. **Test validity.** (a) is invalid as drafted (MUST L1). (b)'s primary test is valid. Its comparison
   is mechanically one-signed (MUST F1).
4. **Lineage and inherited guards.** (a) Every M row has a B row with the same `run_id` and identical
   `p_play` (1,964 of 1,964 rows from 2026-10-21 on; 0 rows where p_play differs). B is therefore
   matched on availability (injury report) and on timing, but not on the context residual. That is the
   right match for "does the residual add something the market moves toward". The Question's phrase
   "information-matched baseline" overstates it (SHOULD L-S1). (b) 10-21 is excluded and the horizon
   starts 10-22: correct.
5. **What it is scored against.** (a) Mid-price movement in probability units, not CRPS or log loss.
   It is a CLV-style information test, not a profit test. (b) Dollar return after taker fees, held to
   settlement.

## Gate re-run (non-negotiable 8), label-free

Command (exit 0). The adversary's copy of the gate script, with B-model, one-rung, sign-agreement
and decoupled-snapshot counts appended. It reads S0 and close mids only to apply the quote filters
and to compare signs. No close-minus-ref difference is formed.

```
uv run python -I <scratchpad>/gate2.py ; echo exit=$?    # exit=0
```

| Count (2026-10-21, 11 games) | Value |
|---|---|
| prop forecasts (M, latest with made_at <= tip − 60) | 1,312 |
| Kalshi prop markets matched to a forecast | 2,129 |
| two-sided at ref and close, spread <= 0.10, >= 30 min apart | 1,507 |
| of those, \|p_M − mid\| >= 0.05 (before one-rung) | 775 |
| player-stat-games after one-rung rule | 470 |
| **qualifying after one-rung rule (as drafted)** | **339** (the draft guessed 100–200) |
| of those, sign(p_M − mid) != sign(p_B − mid) | 139 (41%) |
| B row missing | 0 |
| **qualifying under the MUST L1/L3 replacement text (S0/S1 decoupled, no rung fallback)** | **324**; 30 dropped at S1/close; 136 sign-disagree |
| ref minutes before tip (min / median / max) | 114 / 144 / 149 |
| M made_at before tip (10-21 / 10-22, minutes, median) | 490 / 640 (morning run; no later refresh) |

FAV_DIP: 298 KXNBAGAME snapshots, median gap 1.13 min, 18:56Z–06:59Z. ESPN scoreboard on 10-21 only
from 01:00Z. Today's capture so far (heartbeat): one ESPN file at 15:40Z, status `waiting`, window
21:00Z–05:30Z. Tonight's forecast tips are 23:00Z and 01:30Z.

Both gates PASS honestly. LINE_MOVE: 300 per night is reachable and 14 dates is binding (about
2026-11-04 if every night is captured). FAV_DIP: capture coverage passes, and the horizon is long by design.
The LINE_MOVE gate text understates the post-rule count (SHOULD L-S4).

## LINE_MOVE_TOWARD_MODEL: MUSTs

**L1 (blocker: the rule cannot fail on noise alone). Selection, sign and movement share one noisy snapshot.**
Line 28 qualifies a market on |p_M − mid_ref|, line 29 signs it on (p_M − mid_ref), and the movement starts
from mid_ref. Write mid_ref = T + e, where T is the efficient price and e is quote noise that later reverts.
Then E[m_X] = E[−e · sign(p_X − T − e)] > 0 for any X, whether or not X has information. Selecting on
M's gap over-samples markets where e points away from p_M, and it does so for M, not for B. B's bias is
only diluted through its correlation with M. Part 2 is therefore biased positive under the null. The M − B
difference is not a sufficient guard while selection is on M. The bias is also larger for whichever model
sits closer to the market. Fix: take the sign and selection from snapshot S0, start the movement from a
later snapshot S1, and use a selection for part 2 that is symmetric in M and B. Replace lines 21–31 with:

> - **S0 (sign snapshot)**: the first capture snapshot at or after max(made_at of M, tipoff − 150 min).
>   **S1 (start snapshot)**: the first capture snapshot at or after S0 + 15 min. **close snapshot**: the last
>   capture snapshot strictly before tipoff. S0, S1 and close must each be two-sided (0 < bid < ask < 1) with
>   ask − bid <= 0.10, and close − S1 >= 30 min; otherwise the market is dropped and counted.
> - **mid0, mid1, mid_close** = (bid + ask) / 2 at S0, S1 and close.
> - **p_M(k)** = M's unconditional P(stat >= k) = `p_ge_full` c[k−1]/n (0 when k−1 >= len(c)) × `p_play`.
>   **p_B(k)** the same for B. A null `p_ge_full` drops the market (counted).
> - **One market per player-stat-game**: among the rungs k whose S0 quote passes the S0 filter, the rung whose
>   mid0 is closest to 0.50 (ties: lower k). If that rung then fails the S1 or close filter, the player-stat-game
>   is dropped, with no fallback to another rung, and counted separately by sign(p_M − mid0).
> - **Qualifying (part 1)**: |p_M − mid0| >= 0.05. **Qualifying (part 2)**: |p_M − mid0| >= 0.05 OR
>   |p_B − mid0| >= 0.05.
> - **Movement toward X** (X in {M, B}): m_X = (mid_close − mid1) × sign(p_X − mid0), in probability units;
>   sign(0) = 0.

In part 2 (line 36–37), replace "on the same qualifying markets (selected by M's gap, so the selection cannot
favour B)" with "on the part-2 qualifying markets (selected symmetrically in M and B)". The decoupling
does not remove noise that persists from S0 through S1 (stale quotes). Only the synthetic dryrun in
L-S2 can show how much is left.

**L2 (blocker: the definition is circular). The forecast selection depends on ref, and ref depends on the forecast.**
Line 15 picks "the latest run ... with made_at <= ref time". Line 21 defines ref from M's made_at. Replace
lines 14–16 with:

> - Forecasts: `forward_predictions`. M = the latest `props_context_residual` row per (game_id, player_id,
>   target) with `made_at <= tipoff − 60 min`; B = the `props_recency_v1` row with the same `run_id`, game_id,
>   player_id and target (dropped and counted if absent). `tipoff` is the scheduled tip stored in
>   `forward_predictions`, read as UTC. A snapshot's time is the UTC timestamp in its capture file name. A
>   market's date is its tipoff converted to an America/New_York calendar date.

(On 10-21 every M row had a same-run B row, 1,964 of 1,964.)

**L3 (major: choosing a rung by its close quote looks ahead). The rung choice reads the close quote.**
"Among the rungs k passing the quote filters" includes the close filter. A rung whose price ran to an
extreme (a big move) is replaced by a calmer rung, so the future decides which market is scored. Fixed
by the one-rung text in L1.

**L4 (major: the harness can resolve it more than one way). The bootstrap is unspecified, and `truth/bootstrap.py` does not exist.**
`nba/truth/` has `metrics.bootstrap_ci` (row-level) and `paired_bootstrap_compare` (by game). Neither is
date-clustered. Pooled versus date-mean statistics, the RNG and the row order would all be harness
choices. Replace "the 95% date-clustered bootstrap CI (10,000 resamples, seed 20261022)" on line 34 with:

> the 95% percentile CI from resampling dates with replacement (10,000 resamples, `numpy.random.default_rng(20261022)`;
> dates sorted ascending, markets within a date sorted by (game_id, ticker) before resampling); the statistic
> on each resample is the pooled mean over every market in the resampled dates (each market weighted
> equally); part 2 resamples the per-market difference m_M − m_B the same way.

**L5 (major: non-negotiable 1). A movement statistic on a window date is scheduled around the freeze.**
Line 66–67 allows "the descriptive 11:30 vs 17:30 comparison for 2026-10-22". The draft gives no timezone,
and the capture window opens at 21:00Z, so it is not clear where an 11:30 price comes from. If the
comparison runs before the freeze commit, a direction-of-movement result on a scored date exists
before the rule. Replace lines 66–67 ("No movement statistic ... close snapshot.") with:

> No movement statistic on any date from 2026-10-22 is computed or read before this rule's freeze commit.
> Any descriptive comparison (stated in UTC) runs only after that commit and can change nothing here.

## LINE_MOVE: SHOULDs

- **L-S1.** On line 8, replace "an information-matched baseline" with "the recency baseline from the same run,
  which shares M's p_play (injury-report availability) but not its context residual". This is the honest
  information statement BEST_PRACTICES 2026-10-22 asks for.
- **L-S2 (synthetic dryrun, positive and negative control, before the scoring date).** Before the
  harness reads real mids, run it on synthetic quotes: (i) pure reverting noise with p_M and p_B drawn
  independently of it. Part 2 must be about 0 and the part-1 false-pass rate at most 5% over 1,000
  draws, under the observed spread distribution and stale-quote persistence. (ii) A planted +0.02 move
  toward M must pass. Record both in the review of the harness.
- **L-S3 (14 clusters).** A percentile cluster bootstrap with G = 14 under-covers. Also report the t-interval
  on the 14 date means (df = 13). If the two disagree on the sign of the lower bound, the report says so.
  It does not change the decision unless it is adopted before the freeze. Adopting it makes PASS require both.
- **L-S4.** Correct the gate text on lines 55–58: after the one-rung rule there were 339 qualifying markets on
  10-21 (324 under L1). 300 is reachable in one night, and 14 dates is binding.
- **L-S5.** The 1-cent floor applies only to part 1, which is the more noise-exposed statistic. Consider
  "and mean(m_M − m_B) >= 0.005" in part 2. With a spread cap of 10 cents a 1-cent mean move is
  plausible from noise, so the floor carries little on its own.
- **L-S6.** Late scratches send quotes one-sided (bid 0) and are dropped by the filter. Report the drop count by
  sign(p_M − mid0) and by whether M's p_play < 0.9. Kalshi's DNP resolution for player props should be stated
  in the rule (p_M treats DNP as "No").
- **L-S7.** Scope note for Known limits: M is the morning run (made_at about 8–11 h before tip; no later refresh),
  so the test is whether a morning forecast predicts the last ~2 h of movement.

## FAV_DIP_PAPER: MUSTs

**F1 (blocker for the comparison sentence: mechanically one-signed). The same-game comparison cannot fail.**
On a triggered game the dip entry is at least 10 cents cheaper than the pre-tip entry. When the favourite
wins, the dip trade holds more contracts; when it loses, both lose about the stake. The paired difference is
therefore >= 0 (up to fees) on every game, and "the gain is the favourite, not the dip" can never be
reported. Replace lines 25–27 with:

> ## Comparison (reported, not decisive)
> Buying the favourite at the pre-tip snapshot's ask and holding, on EVERY game with a favourite
> (p0 >= 0.52, pre-tip quote valid), with the same $10 stake, contract and fee math: its mean return per trade
> with its own date-clustered CI, and the difference (FAV_DIP mean − pre-tip mean) with a CI from resampling
> dates jointly. If FAV_DIP passes but its lower bound on the difference is <= 0, the report says the gain is
> buying favourites, not the dip. No same-game paired comparison is used, because on triggered games the dip
> entry is cheaper by construction.

**F2 (major: the pre-tip snapshot can be stale or one-sided). There is no freshness or quote requirement on p0.**
After a capture outage, "the last capture snapshot strictly before tip" could be hours old, or its quote
one-sided. Replace lines 9–11 with:

> - **Favourite**: each game has two `KXNBAGAME` tickers, one per team. The pre-tip snapshot is the last capture
>   snapshot strictly before tipoff (`forward_predictions.tipoff`, read as UTC). It must be no more than 10 minutes
>   before tipoff, and both teams' quotes must be two-sided (0 < bid < ask < 1) with the favourite's ask − bid <= 0.05.
>   Otherwise the game is skipped and counted. The favourite is the team whose own ticker has the higher mid;
>   p0 is that mid. Skip the game if p0 < 0.52 or the mids tie. All later prices are the favourite's own
>   ticker's quotes (never 1 − the other team's).

**F3 (major: the harness can resolve it more than one way). The trigger conjunction and the ESPN period are underspecified.**
The draft does not say whether "first snapshot where the mid ≤ p0 − 0.10" means the first dip snapshot
(then checked for spread and period) or the first snapshot meeting all conditions. It also does not say
how ESPN games are matched, or what halftime, "pre" and OT count as. Replace lines 12–15 with:

> - **Trigger**: the earliest Kalshi snapshot at or after tipoff at which ALL of these hold at once: the favourite's
>   mid <= p0 − 0.10; its ask < 1.00; its ask − bid <= 0.05; and the game is in period 1-3. Period = ESPN
>   `status.period` for this game (matched by home and away team) from the latest ESPN scoreboard snapshot
>   taken at or before the Kalshi snapshot and no more than 180 s before it. Periods 1, 2 (including halftime)
>   and 3 qualify; status "pre"/period 0 and period >= 4 do not. Only when no such ESPN snapshot exists, the
>   Kalshi snapshot qualifies if it is less than 100 minutes after tipoff. The number of trades entered under
>   the fallback is reported.

**F4 (major: the result could depend on config drift). The fee is read from a mutable config.**
`configs/parlay.yaml` today gives coefficient 0.07, an empty `fee_multiplier` and default 1.0. Pin it.
Replace line 17's fee clause with:

> taker fee = ceil_to_cent(0.07 × contracts × ask × (1 − ask)) once per order (multiplier 1.0 for KXNBAGAME, the
> `configs/parlay.yaml` values at this rule's freeze commit; later config changes do not apply to this rule).

**F5 (minor, but the harness must choose): settlement source and voids.** Replace line 18 with:

> - **Exit**: hold to settlement: $1 per contract if the favourite wins by the official final score, else $0.
>   A game postponed, or a market voided by Kalshi, is dropped and counted. No trade is dropped for any other
>   reason. No other exit.

Also, in the primary test (line 22), replace "the 95% date-clustered bootstrap CI (10,000 resamples, seed
20261022)" with the same resampling text as LINE_MOVE L4, with "trades within a date sorted by game_id"
in place of the market sort.

## FAV_DIP: SHOULDs

- **F-S1 (power).** At an entry price of about 0.55 the return per $10 has SD ≈ 10·√(a(1−a))/a ≈ $9. With
  n = 100 and no extra clustering penalty the SE is about $0.90, so the 80%-power minimum detectable effect is
  about **+$2.5 per $10 (25% ROI)**. Put on the signing line: "a NULL here means no edge above ~25% ROI, not
  no edge".
- **F-S2.** Capture gaps: report for each trade the minutes since the previous in-game snapshot. An entry
  after a gap is a fillable price, not lookahead, but a person cannot react faster than the gap.
- **F-S3.** The 0.52 threshold admits near-toss-ups, where a 10-cent dip is ordinary noise. Report results
  for p0 in [0.52, 0.60) and >= 0.60 (reported, not decisive).
- **F-S4.** On 10-21 the ESPN scoreboard started only at 01:00Z. Confirm tonight's capture writes ESPN
  every tick from 21:00Z. If it does, the 100-minute fallback should rarely fire, and its count (F3)
  shows whether it did.

## Inference notes (both rules)

- Multiplicity: LINE_MOVE parts 1 and 2 form an intersection test (both must pass), so no correction
  is needed. FAV_DIP has one primary test. The two rules are separate questions. FAV_DIP inherits a
  search of about 40 rules on 10-21, which the forward-only horizon handles. Do not BH the two rules together.
- Holdout: no 2025 touch. Season 2026 is forward-live only.
- Rule integrity: the drafts were committed at cd8fc93 before any window data existed (the capture's
  first prop snapshot is at 21:00Z). The freeze commit must come before 21:00Z, and the diff from freeze to
  result must be empty.

## Single most likely failure mode if a LINE_MOVE PASS is wrong

Quote noise at the reference snapshot that reverts by close, selected through M's own gap. This is the
`opp_adjusted_ridge` pattern in market form: the selection variable shares a noise term with the
outcome. L1 cuts the shared term. Persistent stale-quote noise survives it, and only the synthetic
dryrun (L-S2) measures what remains.

## What was not verified

- Kalshi's resolution of player props for a DNP (assumed "No" by p_M).
- Whether tonight's ESPN capture runs every tick from 21:00Z (only one file existed at review time).
- Whether `forward_predictions.tipoff` equals the real tip. It is the scheduled time. The ESPN "pre"
  status in F3 protects FAV_DIP; LINE_MOVE's close is "before scheduled tip", which is conservative.
- LINE_MOVE power: not computable without movement data, which was out of scope by instruction. The
  synthetic dryrun in L-S2 is the place to estimate it.
- No harness exists yet. The adversary must check it against the frozen text before the first scoring date.

## Drafted rows (not appended)

DECISIONS (draft): "2026-10-22 | LINE_MOVE_TOWARD_MODEL and FAV_DIP_PAPER pre-freeze review: NOT_READY → READY
once the MUST texts L1–L5 and F1–F5 are adopted verbatim (adversary, docs/reviews/prefreeze_line_move_fav_dip_2026-10-22.md).
Why: selection on M's gap biased M − B under noise; the same-game FAV_DIP comparison was one-signed. Reverses if: the
synthetic dryrun shows residual noise bias in part 2 above 0.002."
