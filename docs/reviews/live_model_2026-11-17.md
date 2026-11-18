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
