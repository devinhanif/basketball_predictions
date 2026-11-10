# ESPN_FRESH_POLLER_NIGHT1: does an 18 s ESPN poll see new plays sooner than the 60 s capture? (DRAFT, NOT FROZEN)

Status: **DRAFT by the market agent, 2026-11-10, answering adversary finding F7 (docs/reviews/espn_fresh_poller_2026-11-10.md).
Not frozen, no result exists, nothing has been read. Freezing is a hand that can commit, before the first poller night is read.**
Measurement only: no decision logic reads any number here.
Advice or momentum: follows advice (adversary F7).

## Lag reading (corrects the 2026-11-09 22:10 DECISIONS row)
With polls `d` seconds apart, first-seen lag = source delay + U(0, d). The offline 60 s median of 50 s therefore implies a
source delay of roughly 20 s, not 50 s; the 50 s includes about 30 s of expected sampling wait. An 18 s poller should show
about 20 + 9 = 29 s if ESPN publishes continuously. The earlier reversal clause "median within 10 s of the 60 s cadence" is
ambiguous and is replaced by the rule below. Newest-play freshness is not box-score freshness: corrected or inserted plays
and stat updates are invisible to this metric (adversary: in 131 same-newest-play/changed-body pairs on 10-28, plays changed
in 46 and boxscore in 46).

## The rule
1. **Unit and population.** A game-night in which both the poller (`<out>/fresh/fresh.jsonl`, endpoint `espn_summary_fresh`)
   and the 60 s capture (`espn_summary` bodies in `manifest.jsonl`) fetched the game while ESPN state was `in`. Same games, same nights
   only; the capture runs alongside. Core-API rows are descriptive and not part of the test.
2. **Per-game statistic.** For each game-night, the median first-seen lag (fetch time minus the newest play's wallclock, on
   fetches where that play is new to that source; the first fetch of a game excluded) for each source. Paired difference
   `delta_g = median_capture_g - median_poller_g` (positive = poller fresher). Games with fewer than 20 new-play
   fetches in either source are dropped and counted.
3. **Claim "the poller shows a gain" iff** the median of `delta_g` over games is >= 15 s AND the one-sided 95% lower bound of
   `nba/truth/metrics.py::bootstrap_ci` applied to the per-game `delta_g` (game-clustered by construction: one value per game;
   2,000 draws, seed 0) is > 0. One test, m = 1. Otherwise: no gain shown; report the point value and interval.
   **What the 15 s floor separates (stated so a pass is not read as a discovery).** If ESPN publishes continuously, the
   mechanical expectation of `delta` is (60 - 18) / 2 = about 21 s, simply from sampling more often. If ESPN batches
   server-side at 60 s or more, `delta` is about 0. A pass therefore means "ESPN publishes more often than the 60 s capture
   samples", the expected result of a shorter interval; it does not mean ESPN is fast. A fail means batching at or above the
   60 s scale (or too few games). Neither switches any decision.
4. **Minimum n.** At least 30 game-nights with both sources. Below that: "insufficient sample", no claim.
5. **Reported with it (descriptive, never a gate):** per-source median and p90 lag with n; unchanged-fetch rate; n 304s (is
   there any validator support); `Age`/`Date` header summary; error, backoff and `fresh_parse_bad` counts; per-night request
   count against the estimate; poller cycle duration (`fresh_cycles.jsonl`), and the observed per-game fetch gap used as `d`.
6. **Invalid night (summary endpoint only decides):** the night is reported and excluded from the test, with the reason, if
   `fresh_stopped_reason` is set, OR `fresh_parse_bad_summary` > 0, OR `fresh_auth_fail_game` > 0 (any 401/403 on a summary or
   core request, stopped or not; this catches a single-game 403 stretch hidden by healthy scoreboard polls), OR
   `fresh_last_game_success_ts` is empty on a night with live games, OR more than 5% of cycles exceeded `interval_s`.
   `fresh_parse_bad_core` > 0 does NOT void a night: the core endpoint is descriptive, so a core failure is reported and its
   rows dropped, and the summary data stays valid.
   **Poller restart mid-game:** rows after a restart are `first_poll` (the in-memory last-play memory is empty) and are
   excluded from lag, as for the first fetch of any game; the game-night is kept if it still has at least 20 new-play fetches
   in both sources after exclusion.
7. **No variants.** No new window, floor, statistic or endpoint after the first poller night is read; a new question gets a
   new rule. Nothing here is a stake, a promotion or a model change.

## Budget (corrects the 12k estimate)
About 10k requests on an average night (offline: about 930 live game-minutes per night x 3.33 cycles x 3 requests, plus
about 800 scoreboard calls), about 22k on a 15-game night. Storage upper bound about 93 MB per night (about 3,100 bodies at
about 30 KB gz; 5.3% of consecutive 60 s bodies are byte-identical, so "store only changed" saves little). Disk was 97% used
with 14 GiB free at review time: Devin's call before the first night.

=== RESULTS BELOW ===
(none)
