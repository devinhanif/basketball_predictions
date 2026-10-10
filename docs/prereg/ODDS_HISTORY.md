# ODDS_HISTORY - did the props model beat the market on past seasons? (pre-registration)

Status: FROZEN 2026-10-10 13:40 CT (Devin: "confirmed" at 13:30). The 13:30 freeze (sha ecdcf2d2) was VOID: its
hash was computed by a substring-marker scan that started at the header's own verify line, so it covered the
line recording the hash and missed section 8, which a bad insertion had left in the header. No model row had
been joined to a price under either freeze. Scoring may start only after the sha below is committed.
Verify: `awk '$0=="<!-- FROZEN-END -->"{f=0} f{print} $0=="<!-- FROZEN-BEGIN -->"{f=1}' docs/prereg/ODDS_HISTORY.md | shasum -a 256`
sha256 of the frozen section: d5b8b1626594bf482841633f29b73918da15cd045cd6f4d765fd778e1f4566b5

<!-- FROZEN-BEGIN -->
## 0. Question

For seasons 2023-24 and 2024-25 (and once, logged, 2025-26), does the production props model's
probability of clearing the market's line beat the market's own de-vigged probability, at the time the
model would have predicted (T-60) and at the last pre-tip line (T-5)? And would a blend of the two beat
the market alone? Source of prices: the-odds-api.com historical snapshots (5-minute grid; "closest
snapshot at or before"), regions us (DraftKings, FanDuel, ...), eu (Pinnacle), us_ex (Novig, ProphetX).
Source of model rows: the production props arm's walk-forward out-of-fold predictions on seasons 2023
and 2024, produced by the same harness that scored LOWER_TAIL (month blocks, train strictly before the
block, integer support). No model is refit for this test.

## 1. Units and pairing

1. Unit = (stat, game_id, player_id, book, line, snapshot_kind). Stats: pts, reb, ast, fg3m. Books scored
   separately: `pinnacle` (sharp), `draftkings`, `fanduel` (what a US bettor faces), and a `consensus`
   (median de-vigged probability across all books quoting that player-line). Only two-sided quotes.
2. One line per (player, book, snapshot): the book's main line (the line with the most balanced prices;
   ties → lower line). Alternate lines are not scored.
3. Pairs need a model OOF row for the player-game and a quote; one-sided rows are counted
   (`no_quote`, `no_model_row`, `one_sided`, `unresolved_name`, `unmatched_event`) and reported.
   Name resolution through the reviewed alias table only; the unresolved list is written.
4. DNP player-games are excluded identically from both sides and counted (the books void them).
5. Model probability: P(over L) = P(Y >= k+1) for L = k+0.5 from the OOF integer-support thresholds;
   whole-number lines score over vs under conditional on no push, push mass removed from both.
6. Market probability: multiplicative de-vig of the two American prices (power method as a sensitivity
   column, fixed in advance).

## 2. Hypotheses (stated expectations: H1 and H3 likely fail; H2 may show w ≈ 0)

- H1 (props skill): model log loss < market log loss on over/under, per stat, per book, per snapshot.
- H2 (blend): `logit(p) = logit(p_mkt) + w (logit(p_model) - logit(p_mkt))`, w fit by log loss on
  2023-24 per stat (grid 0..1 step 0.05), scored frozen on 2024-25: blend log loss < market log loss.
- H3 (games): `rung0_injury_elo` OOF win probability vs the de-vigged Pinnacle moneyline (else consensus)
  at T-60; log loss.

## 3. Metrics, inference, floors

- Primary: paired log loss delta (model or blend minus market), negative = better. Secondary: Brier,
  reliability in ten bins, and the T-60 → T-5 line move in the model's direction (CLV sign agreement).
- CI: percentile bootstrap clustered by `game_id` (all props of a game resampled together), B = 5,000,
  `numpy.random.default_rng(2026)`; two-sided p from the bootstrap.
- Practical floor: delta <= -0.002 per unit with the CI upper bound < 0 to claim "beats the market";
  |delta| < 0.002 is "indistinguishable"; otherwise "market better".
- Multiplicity: family per snapshot kind = {pts, reb, ast, fg3m} x {H1, H2} x {pinnacle, consensus} = 16,
  Holm; DraftKings/FanDuel columns are reported, not tested. H3 is its own single test.
- Money (descriptive, reported only if H1 or H2 passes for that stat/book): realised return per unit stake
  of pairs with model edge >= 0.03 at T-60 at the book's own price (the price contains the vig; no further
  fee), with the same clustered CI. A positive-EV claim needs the CI lower bound > 0.

## 4. Seasons and the holdout

- Fit anything that is fit (de-vig choice is fixed; only w in H2) on 2023-24; select and report on 2024-25.
- 2025-26 is the frozen holdout: ONE logged touch (docs/HOLDOUT_ACCESS_LOG.md row written before the run)
  for the frozen production arm and the frozen w only, books and snapshot kinds fixed as above. Kalshi
  2025-26 props ladders are a secondary analysis inside that same touch.

## 5. Minimum data checks (fail = stop, report, no model result)

1. Event matching: >= 99% of games with a real tip-off in each season matched to exactly one event.
2. Props posting: a Pinnacle or consensus two-sided player_points quote at T-60 for >= 60% of
   player-games with >= 20 minutes played, per season (reported per book).
3. Name resolution >= 97% of posted props after alias review.
4. Snapshot timing: requested T-60 snapshot timestamp <= tip - 55 min for >= 99% of games (never after
   the model's as-of).
5. Leakage guard: any paired row with snapshot_ts > model as-of voids the run.

## 6. Slices (reported, no decision weight)

Volatility bucket; scoring tier; starter/bench; line distance |L - model mean| / model std in thirds;
over vs under; home/away; first 15 team games vs rest; teammate-OUT games; book; T-60 vs T-5.

## 7. Outcomes

Pass on 2024-25 for a stat and Pinnacle at T-5: strongest result; promote-candidate for the budget tool's
market-as-prior weight and a DRAFTED holdout row. Pass at T-60 only: timing edge; report. Fail: the
market is better; recorded as a finding; the budget tool keeps saying "keep your money" for that stat.
## 8. Choices fixed before the freeze (the scorer's `CHOICES`; settled 2026-10-10 after building, before any join)

1. Model probabilities are clipped to [0.5/199, 1 - 0.5/199] before log loss (the 199-point integer grid can
   emit exact 0 or 1); market probabilities are not clipped.
2. Consensus line = the modal main line across books (ties to the lower line); consensus probability = the
   median multiplicative de-vig over every book with a two-sided quote at that line; no minimum book count,
   the number of books is reported per cell.
3. The blend weight w is fit per (stat, book, snapshot kind) cell on 2023-24 and scored frozen on 2024-25.
4. "Beats the market" = point <= -0.002 AND CI upper < 0 AND (for tested cells) Holm p < 0.05.
5. A negative delta whose CI includes 0 is labelled "market better (negative delta, CI not below 0)"; the
   literal label is kept, the qualifier is printed.
6. Holm is applied on 2024-25 over the 16-test family per snapshot kind; on 2023-24 over H1 only (8 tests);
   H2 is not scored on 2023-24 (it is fit there).
7. Leak guard: the T-60 snapshot must be at or before tip - 60 min (stricter than gate check 4's 55 min);
   applied to T-60 pairs only, since T-5 is later by design.
8. EV is reported only for T-60 cells that pass; edge = p_model(side) - de-vigged market p(side) >= 0.03;
   consensus has no own price and gets no EV; DraftKings/FanDuel are reported, never tested.
9. Slices: volatility = recency std / max(recency mean, 1) in thirds of the stat's model-row universe;
   tier = recency mean in thirds; line distance in thirds of the pooled pairs; side = the model's lean;
   pushes are excluded from paired and EV sets alike.
10. Gate check 1 is satisfied by "the game has prices in the table" (the exactly-one-event check ran in the
    pull and is recorded there).
<!-- FROZEN-END -->

## Results (run 2026-10-10 13:27 CT under sha256 d5b8b162; ledger T203-T202; adversary pending on the reb/Pinnacle blend cell)

Fill from the tables above only; do not add a claim the tables do not carry. State n, the clustered CI and the family corrected over for every sentence; say plainly when the market was better.

## Amendments (append only, dated)
(none)

## Pre-freeze gate check (2026-10-10 03:50 CT, prices only; no model row joined; outside the frozen text)

Run on `data/odds/odds_history.duckdb` after the props pull completed for all three seasons
(7,893 game-snapshots; 8.8M prop rows; Pinnacle rows 71k-80k per season-snapshot). Non-negotiable 8.

| Check | 2023-24 | 2024-25 | 2025-26 | Threshold | |
|---|---|---|---|---|---|
| 1. Games matched to exactly one event | 1318/1318 | 1315/1315 | 1316/1316 | >= 99% | pass |
| 2. Pinnacle two-sided player_points at T-60, players with >= 20 min | 66.2% | 68.1% | 74.4% | >= 60% | pass |
| 2b. Any book, same | 81.3% | 85.3% | 87.8% | reported | |
| 3. Prop rows with a resolved player id | 99.1% | 98.9% | 99.9% | >= 97% | pass (before the 2024 reparse that applies six reviewed aliases) |
| 4. T-60 snapshot at or before the requested time | 100% | 100% | 100% | >= 99% | pass; 99.9% / 100% / 100% within 5 min |

Unresolved names left (reviewed by hand, see configs/kalshi_aliases.yaml): vendor nicknames such as
Cameron/Cam Thomas, Vincent/Vince Williams Jr, Joshua/Josh Giddey, "Jalen (2001) Johnson", Mohamed/Mo
Bamba. The rule can be frozen; the model-row join starts only after Devin confirms it.
