# Live model: feed delay, a rule for the in-game line model, and a reading log (2026-11-17)

Author: modeler. Branch `agent/modeler-live-model` off `main` 55ccf5ca. Asked by Devin through the main session (2026-11-17):
he wants a live in-game model whose predictions can be tracked in the app and asked what to research to make it fast enough.
Order chosen by Devin: (1) measure how far the live feed runs behind the market, (2) freeze a proper rule for the in-game line
model that already logs to the Discord cards, (3) the reading.

**Advice or momentum.** Part 1 and Part 2 follow advice: the decisions review of 2026-11-17 (row 6, branch
`agent/archivist-decisions-review`) says the in-game logs accrue without a frozen rule and should be frozen or declared
descriptive; HALFTIME_OVERS_VS_PRICE 12.12 C6 already proposed a calibration rule as the testable alternative. Part 3 is Devin's
request. Claude's standing view, stated up front: the live win probability is heavily priced by the market (MODEL_PLAYBOOK, neural
sequence class) and T508's in-game gain was 94% a weak baseline; a live model is worth building as a display and a teaching tool,
and as a bet signal only if a frozen forward test says so.

Section order is the commit order: the Part 1 method was committed before any delay was computed; the Part 1 results, Part 3 and
the proposed rows came in later commits.

---

## Part 1 method: how far behind is our feed? (written and committed before any result)

**Question (descriptive, no decision rule, no claim):** when a scoring play happens, how many seconds pass before our captured
ESPN feed shows it, compared with ESPN's own event time and with when the Kalshi game-winner market moves?

**Nights.** Every ET night with both the live capture (`data/raw/live_<date>/`, `espn_summary` and `espn_scoreboard` in
`manifest.jsonl`) and a finalized 15-second Kalshi capture (`data/kalshi/candles15/<date>/compacted/{quotes,trades}.parquet`):
**2026-11-10, 11-11, 11-12, 11-13, 11-14, 11-15, 11-16** (7 nights). 2026-11-17 is excluded: games are in progress and the
candles night is not finalized. Nights before 11-10 have no 15-second quotes. Season 2026 only; no season-2025 file is read; no
DuckDB file is opened.

**Excluded source, on purpose.** The ESPN freshness poller (`fresh/`, `fresh.jsonl`, endpoint `espn_summary_fresh`) is NOT read.
`docs/prereg/ESPN_FRESH_POLLER_NIGHT1.md` is an unfrozen draft whose statistic is the poller-minus-capture lag; it says it must be
frozen "before the first poller night is read". Reading it here would burn that rule. The 60-second capture's own lag is not new
information (that draft already quotes its offline median of about 50 s), so this measurement does not burn it. The poller's
number belongs to that rule once frozen.

**Events.**
1. A *scoring play* is a play in an `espn_summary` body with `scoringPlay == true` and `scoreValue >= 1`, identified by its `id`.
   Its **source event time** `w` is the play's `wallclock` (ESPN's own wall clock, UTC, whole seconds).
2. Its **first sighting** `t_seen` is the manifest `received_at` (our clock) of the first `espn_summary` file of that game whose
   `plays` list contains that `id`. A play first seen in the game's first in-progress summary is excluded (it may have happened
   before we were polling); so is a play whose `wallclock` is missing or unparseable (counted).
3. **Scoreboard sighting (secondary):** the `received_at` of the first `espn_scoreboard` file at or after `w - 5 s` in which both
   teams' scores are at least the play's `homeScore` / `awayScore`.

**Feed-delay statistic A.** `A = t_seen - w` per scoring play (seconds), pooled over all scoring plays; also `A_sb` for the
scoreboard. Reported: n plays, n games, median, 10th and 90th percentiles, and a game-clustered 95% interval on the median
(percentile bootstrap: resample games with replacement, recompute the pooled median, B = 2,000, `numpy.random.default_rng(0)`).
Also per night.

**Market comparison B (only on events where a market move can be attributed).** A scoring play is a *qualifying event* iff:
- **isolated:** no other scoring play of the same game has `wallclock` in `[w - 90 s, w + 60 s]` (free-throw pairs, and-ones and
  quick answers are excluded because a move cannot be assigned to one of them);
- **reference quote exists:** the scoring team's game-winner ticker `T_s` (Kalshi suffix = the scoring team, ESPN abbreviations
  mapped by `nba.markets.halftime_entry.ESPN_TO_KALSHI`) has a 15-second quote with `fetched_at <= w - 45 s` (the last such
  quote is the reference, and it is at most 60 s old at `w - 45 s`) with both bid and ask present, `0.03 <= mid <= 0.97` and
  `ask - bid <= 0.05`;
- **material:** the Brownian-motion change it should cause is at least 0.03, where `p0` is the reference mid,
  `r` the share of a 48-minute game left at the play (`(2880 - E) / 2880`, `E` the elapsed regulation seconds from period and clock;
  overtime `r = remaining overtime seconds / 2880`; floored at 10 s / 2880), `s = scoreValue`, sigma = 12 points per full game,
  and `dp = Phi(Phi^-1(p0) + s / (sigma sqrt(r))) - p0`. sigma = 12 is fixed here, before any result, as a round value of the
  final-margin spread around a closing line; it is NOT Stern's 1994 estimate (I could not open that paper's scan; see Part 3). It
  only selects which plays are big enough to expect a visible move; it is not part of any delay.

**Market move.** Window `(w - 45 s, w + 180 s]`.
- *Quote move* `t_q`: the `fetched_at` (our clock) of the first 15-second quote of `T_s` in the window with
  `mid >= p0 + 0.02`.
- *Trade move* `t_tr`: the `created_time` (Kalshi's clock) of the first print in the window either on `T_s` with
  `yes_price >= ref_ask_s + 0.01`, or on the opponent's ticker `T_o` with `yes_price <= ref_bid_o - 0.01` (its own reference
  quote, same rule). Prints are the candles store's own pulls united with the capture's `kalshi_trades` (one row per `trade_id`).
- *Market move time* `t_m = min(t_q, t_tr)`; no move in the window is recorded as "no move".
- *Placebo (the same definition in the wrong direction):* `mid <= p0 - 0.02`; `T_s` print `<= ref_bid_s - 0.01`; `T_o` print
  `>= ref_ask_o + 0.01`. Its rate says how often the definition fires on noise.

**Statistic B.** On qualifying events with a move: `B = t_seen - t_m` (positive = the market moved before our feed showed the
play) and `t_m - w`. Reported: n events, n games, median, 10th-90th percentiles, game-clustered 95% interval on the median (same
bootstrap), the share of qualifying events with `t_m < t_seen` (the market had already moved when our feed first showed the play,
denominator stated both as "events with a move" and "all qualifying events"), the move rate, the placebo rate, and the same split
by quarter (Q1-Q3 vs Q4/OT). Trade-only and quote-only versions are printed beside it.

**Clocks.** Three clocks meet: ours (`received_at`, `fetched_at`), ESPN's (`wallclock`) and Kalshi's (`created_time`). Our clock
against Kalshi's is checked as the median of `fetched_at - server_date` (Kalshi's HTTP Date header, whole seconds) per night; an
offset beyond 2 s is reported and subtracted. ESPN's wallclock cannot be checked against an independent clock; it is what ESPN says
the play's time was and may itself lag the real moment.

**What the polling interval contributes (the floor).** The capture fetches each game's summary about every 60 s, so even a source
that published instantly would show a sighting lag uniform on 0-60 s (mean 30 s, median 30 s). The measured spacing of consecutive
summaries per game is reported. The 15-second quotes add up to 15 s to `t_q`; trade prints carry Kalshi's own time and add none.

**What would make it uninformative.** (a) Fewer than 100 qualifying events with a move or fewer than 20 games: B is printed
descriptively and called "too few"; (b) placebo rate at least half the real move rate: the move definition does not isolate the
event, and B is labelled uninformative; (c) a night whose `derived/reconciliation.json` union print coverage is below 0.9: its
trade moves are flagged and B is printed with and without that night; (d) a clock offset I cannot correct. Nothing above is
re-chosen after a result: no other threshold, window, sigma or event definition is tried.

Data version: the files as they stand at the commit of this section (write-once pages; the manifest is append-only).

---

## Part 1 results (computed after the method commit 17404a4c; descriptive, no claim)

Code: scratch only (`extract.py`, `analyze.py` in the session scratchpad; not committed, read-only on the capture). Data version:
`data/raw/live_2026-11-{10..16}/manifest.jsonl` and `data/kalshi/candles15/2026-11-{10..16}/compacted/` as of 2026-11-17 ~20:30 CST.
Every interval is a game-clustered 95% percentile bootstrap of the pooled median (B = 2,000, seed 0) unless stated.

**Coverage.** 7 nights, 51 games (51 of 51 scheduled), 6,181 scoring plays; 10 excluded as first seen in the game's first live
summary, 0 without a wallclock. Kalshi print coverage (union of own pulls and capture) against the official 1-minute candles: minute
coverage 1.00 and volume coverage 1.00 on every night, so criterion (c) never fired.

**A: our ESPN summary feed vs ESPN's own event time.**

| | n plays | games | median (s) | 95% CI on median | p10 - p90 (s) |
|---|---|---|---|---|---|
| summary (`t_seen - wallclock`) | 6,171 | 51 | **66.8** | [65.8, 67.6] | 40.3 - 94.6 |
| scoreboard (secondary) | 6,167 | 51 | **46.6** | [45.5, 48.1] | 20.5 - 72.3 |

Per night the summary median ranges 65.5 to 67.5 s (each night's CI inside 63 to 69). No play was ever seen before its wallclock
(0.0%). **Polling interval:** consecutive summaries of a game are 60.1 s apart (median; p10 59.96, p90 60.26; 7,139 gaps), so polling
alone contributes about 30 s on average (uniform 0-60 s). What is left is ESPN's own publishing delay: about 37 s for the summary's
play list and about 17 s for the scoreboard score (the scoreboard is also fetched about once a minute, 480 files a night; I did not
measure its spacing separately). The scoreboard shows a new score about 20 s before the summary lists the play.

**Clocks (a disclosure about the method).** The method said a Kalshi clock offset beyond 2 s would be "reported and subtracted". The
median gap between our `fetched_at` and Kalshi's HTTP `Date` header is 3.1 to 11.5 s by night (3.4, 3.1, 9.3, 11.0, 6.9, 11.5, 6.0
for 11-10..11-16), but it is **not a clock offset**: our trade pulls received prints as early as 0.07 s after Kalshi's own
`created_time` (0.1% quantile; minimum -0.07 s on 11-12, -0.29 s on 11-16), so our clock agrees with Kalshi's trade clock to within
about 0.3 s. The Date gap is the age of the `/markets` response itself (a cached or batched body), which means **the 15-second
quotes are 3 to 11 s older than their `fetched_at`**. Subtracting it as a clock offset would be wrong, so nothing is subtracted. Had
it been applied literally, A would fall by about 6 s; B on quotes is unchanged (both of its times are on our clock).

**B: our first sighting vs the first market move** (qualifying events: isolated, reference quote OK, Brownian move >= 0.03).

Funnel: 6,171 plays -> 271 isolated -> 225 with a usable reference quote -> **207 qualifying events in 47 games** (expected move
median 0.073, p10 0.038, p90 0.151).

Pre-declared verdict on the combined move definition: **UNINFORMATIVE by criterion (b).** The market "moved" on 0.995 of events but
the placebo (same rule, wrong direction) fired on 0.667, more than half the real rate; the placebo fired before the real move on
0.391. The combined statistic is printed only for completeness: B median 89.5 s [82.8, 92.4], p10-p90 54.4-126.5, n 206; move time
minus wallclock median -20.5 s. Those numbers say the trade rule fired at the start of the window on noise, not that the market saw
the play 20 s before ESPN.

**Diagnostic, decided after seeing the combined result (disclosed; not a re-chosen definition):** the two pre-declared components
split cleanly. Trade rule: rate 0.995, placebo 0.662 (any print one cent through the earlier ask within 225 s is common in a market
with 150,000 to 550,000 game-winner prints a night). **Quote rule: rate 0.952, placebo 0.237** (it would pass criterion (b) on its
own). The quote-only version, which the method listed to be printed beside the combined one:

| quote-only (n = 197 events with a quote move, 47 games) | median | 95% CI | p10 - p90 |
|---|---|---|---|
| quote move minus wallclock (s) | **+30.0** | [27.1, 33.7] | -20.2 - +52.3 |
| our summary sighting minus quote move (s) | **+38.6** | [33.7, 43.5] | +4.3 - +92.5 |
| our scoreboard sighting minus quote move (s) | **+19.9** | [14.6, 26.1] | -22.3 - +77.3 |
| share of events where the quote had moved before our **summary** showed the play | **0.919** | [0.884, 0.952] | |
| share where the quote had moved before our **scoreboard** showed the score | **0.761** | [0.701, 0.815] | |

Two things make these lower bounds on the market's lead: the quote is polled every 15 s (mean 7.5 s of our own wait) and the quote
body is itself 3 to 11 s old when it arrives. The market's real move is therefore roughly 10 to 20 s earlier than `t_q`, i.e. at or
shortly after ESPN's wallclock. By quarter (combined definition, same placebo caveat): Q1-Q3 155 events, Q4/OT 52; no difference
worth reading.

### Conclusion (one paragraph)

Our live feed shows a scoring play a median **67 s** after ESPN's own timestamp (80% of plays between 40 and 95 s; 6,171 plays, 51
games); about 30 s of that is our 60-second polling and about 37 s is ESPN's publishing delay; the scoreboard shows the new score
about 20 s sooner (median 47 s). On material, isolated scoring plays the Kalshi game-winner quote had **already moved before our
summary showed the play on 92% of them (95% CI 88-95%, 197 events, 47 games), by a median of at least 39 s**, and before our
scoreboard showed the score on 76%. So a live chance computed from this feed is **honest to display** if it carries its own
staleness ("as of Q3 4:12, 1 min ago"), but it is **not something to act on against Kalshi**: on most plays that matter the market
has left it behind before we compute it. What would shorten it, with terms status from DECISIONS: (1) **polling cadence**, the
cheapest lever: 60 s to 15 s cuts the polling share from about 30 s to about 7.5 s; the ESPN fresh poller already runs at 15 s
under Devin's override (DECISIONS 2026-11-09 21:35 and 2026-11-10 10:45; ESPN's own terms were never read, flagged there), and its
effect belongs to ESPN_FRESH_POLLER_NIGHT1 once that is frozen (I did not read its files); (2) **use the scoreboard score and clock
for the live win chance** and the summary only for box stats: about 20 s fresher, no new request; (3) **a faster public source**:
cdn.nba.com liveData is NOT cleared (DECISIONS 2026-11-09 21:33, nba.com terms s.9 bar gambling use; not covered by the override),
and stats.nba.com returns nothing for games in progress (DECISIONS 2026-10-21 20:05). No cleared source can close the gap to zero:
even with instant polling, ESPN's own 17 to 37 s publishing delay sits behind a market that reacts at about the wallclock.
