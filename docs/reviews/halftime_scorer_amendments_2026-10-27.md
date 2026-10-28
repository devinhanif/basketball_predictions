# Proposed amendments to HALFTIME_OVERS_VS_PRICE (draft, agent/modeler-halftime-overs 5a8d140), from the halftime scorer review

Source: `docs/reviews/halftime_scorer_2026-10-27.md` (adversary, NOT_READY on e02514e); fixes in the scorer branch
`agent/market-jarvis-halftime-scorer`. These are TEXT PROPOSALS for the prereg branch, which is separate; nothing here
edits a frozen rule (the draft is not frozen). A1, A2 and A3 change the entry set, so **f must be re-measured under the
amended text before the gate decision**: the earlier f (0.237 pooled, 219 of 926 units; 0.196 under a 10c cap, 166 of 847)
was measured under the old step 1 and without A2, and is a label-free gate count, not a claim.

## A1. Section 2 step 1: what a halftime tick is (adversary M1)

Replace step 1 with:

> 1. **Halftime start.** `t_ht` is the `received_at` of the first `espn_scoreboard` file in which the game has
>    `status.type.name == STATUS_HALFTIME`, `status.period == 2` and a game clock of zero (`displayClock` equal to
>    `0.0`, `0:00` or `0`), **and** the immediately preceding scoreboard file that shows the game has `period == 2` and
>    was received at most 150 s before. If the first such tick has no such predecessor the game has no entry
>    (`ht_start_unobserved`, counted): the capture started mid-halftime and `t*` would not be halftime + 120 s.
>    The decision time is `t* = t_ht + 120 s`. The **second half has started** at the first scoreboard file after
>    `t_ht` with `period >= 3`; a game with such a file at or before `t*` has no entry (`q3_before_decision_time`). A
>    status change that stays in period 2 does not end the half.

Why: on 2026-10-26 ESPN sent `STATUS_HALFTIME` with period 2 and 11:04 left (MIN@CLE, scoreboard and summary, 60 s). The
old rule took it as halftime, then took the next in-progress tick as "second half started", and lost the real halftime
at 00:15:13Z. Had it lasted 120 s a Q1 box would have been scored as the e = 24 state. Capture on 10-21..10-26: 4 of 30
halftimes were first seen with no earlier tick; they are excluded by the predecessor clause.

Also apply the same test to the summary in step 3: its header must read `STATUS_HALFTIME`, `period == 2`, clock zero
(362 of 364 halftime-status summaries on the six nights do; the other two are the glitch and a Q1 end).

## A2. Section 2 step 3: the quote tick must be complete (adversary M2)

Add to step 3:

> The Kalshi "tick" is the first in-window page of the series and every page of that series received within 30 s of it.
> A live scorer reads the tick only when `as_of >= first page + 30 s`; before that the snapshot is not taken
> (`tick_not_closed`, counted) and the next pass takes the whole tick. A replay sees the whole capture and is unaffected.

Why: the pages of one tick arrive over a median 5 s (max 29.7 s on 10-26). A live pass inside that span froze a partial
snapshot and the series was never completed, so live rows differed from a replay of the same night and f's denominator
was biased.

## A3. Section 2 step 5: the spread filter (adversary recommendation; before the freeze)

Add to step 5:

> Only quotes with **`bid_c >= 1` and `ask_c - bid_c <= 10`** (integer cents, from the same page) are eligible entries.
> Books that fail are counted (`x_wide_or_no_bid`) and reported as a pre-declared descriptive slice, never entries. The
> secondary comparisons against the mid (section 5) use the same rows.

Why: the primary metric buys at the ask, so a wide book does not fake the price, but the largest apparent edges sit on
lone resting asks (the 2026-10-26 top row was ask 38c, bid 2c, model 89%), which is where adverse selection and the
120 s + 180 s delay bite hardest, and a mid of such a book is not a price. Chosen from the label-free spread distribution
(median 6c, p75 12c, p90 17-20c), not from edges.
Label-free counts on the six gate nights under the old text: 847 of 926 units survive a 10c spread cap (91%); of the 219
flagged units, 166 survive (76%). Disclosure (the adversary's and mine): the demo model edges by spread bucket were seen
label-free before the cap was chosen; no outcome, settlement or final was read. The JARVIS display applies the same
filter now (a display choice, not the rule).

## A4. Section 3 last bullet and section 4: how the forward log is stamped (adversary B1)

Replace "Model P's are written to a forward log with `made_at <= t* + 180 s`" with:

> Model P's are written to a forward log whose every row carries `written_at`, the **database's** wall clock at insert
> (not caller-set), and `mode` (`live` or `replay`). A row is **in the prereg window** iff `mode = live` and
> `written_at <= t* + 180 s`; a replay row is never in the window. Rows outside the window are stored, flagged and
> **never scored**. A replay may not write to the live store, and a store holds one mode only. A row whose
> `written_at` is after the second half started is refused; the refusal is logged once per (game, series). `f` and the
> entry count `n_req` use live in-window rows with `n_prior >= 5` only.

(This also aligns the draft with the code on adversary m8: the draft said the scorer "refuses" a row with `made_at`
later than `t* + 180 s`; the code stores it flagged. Stored, flagged, never scored is the proposed text.)

## Text-vs-code alignment notes (no behaviour change)

* **m2, section 3 "Train-serve parity on minutes".** Replace "the refit uses stint minutes rounded to the nearest integer at
  every checkpoint" with: "the refit uses unrounded stint minutes; the served state uses ESPN's integer `MIN`. Measured on
  season 2024 at e = 24 with the shipped artifact (adversary check 2b): mean |dp| 0.0009 / 0.0014 / 0.0009 / 0.0012
  (pts / reb / ast / 3PM) under rounding, at most 0.72% of rows move by more than 0.05, log-loss change at most +0.0001.
  ESPN's floor-or-round convention is still to be checked against stint time before the freeze."
* **m3, section 3.** Add: "Platt is fitted by unregularised maximum likelihood (sklearn, C = inf), not the harness's
  25-step undamped Newton `rule.platt_fit`, which the T508 attack showed can diverge on per-checkpoint fits; on the pooled
  fit the two agree. The fitted pairs are in the artifact (`platt`: intercepts 0.005-0.023, slopes 1.00-1.03)."
* **glm_e (section 3).** State that the glm_e B0p term is a reconstruction (K = 40 on the rate prior; share prior = recent
  minutes / 48), because the attack's scripts were not kept, and that at e = 24 it is as good as M on 2024 in-sample
  (adversary 2c: pts -0.0006, reb +0.0020, ast +0.0000, 3PM +0.0001 M minus glm_e, so PASS_BASELINE is the likely ending).
* **m1.** `f` in section 8 is the share of units whose best live in-window strike has `p_M - ask - fee >= 0.05`, counting
  only rows scored with `n_prior >= 5`. The store records `min_prior` per row and `mode`; its `share_f` applies both.
* **Recency window (C3).** Devin's C3 = allow (holdout-log row 105a6dc): the live scorer's pregame mean is the last 10 played
  regular-season games before the game date, including 2025-26 games in October-November, as T508 trained on and the daily
  pipeline reads. Say so in section 1 and name the row.
