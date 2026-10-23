# Pre-freeze review: KALSHI_VS_PINNACLE_PAPER (T516), 2026-10-22

Reviewed: `docs/prereg/KALSHI_VS_PINNACLE_PAPER.md` at 2dc0542 (branch agent/market-kalshi-pinnacle), the gate scripts
`research/scratch/kalshi_pinnacle_gate_2026_10_22.py` and `research/scratch/softbook_pinnacle_hist_gate_2026_10_22.py`.
The MUSTs in `docs/reviews/prefreeze_star_floor_2026-10-22.md` carry over (M1 regular season, M4 exact arithmetic,
M5 settlement, M6 horizon). Devin's defaults apply: m = 0.03 and N = 15. Stage A is the decisive test. Stage B is
descriptive only.
Model class: no model is fitted. This is a market-vs-market paper trading rule (playbook: market and benchmark). No
outcome or return was computed in this review. Season 2025 was never loaded. Every DuckDB connection was `read_only=True`.

**NOT_READY as written; READY_TO_FREEZE once M1-M10 are applied verbatim.**

Verdict: BLOCKED

## Top finding
**The report season has already been read for outcomes on a near-identical rule.**
`docs/reviews/ledger_audit_2026-10-12_part3.md:124` (2026-10-12) computed the realised ROI of "betting the DK side
Pinnacle favours when |gap| >= 3 pts" at T-60. The result was +0.8% [-1.1%, +2.6%] (n 10,015), split 2023 +2.4% and
2024 -0.8%; at >= 5 pts it was +1.2% [-3.7%, +6.0%] (n 1,343).
- Both 2023 and 2024 are therefore outcome-touched for this question.
- The 0.03 margin matches that cut.
- The draft's Lineage section omits this, and its claim that the thresholds were "chosen from label-free counts only"
  is true only of this draft.

This is not a holdout breach, because 2025 was never involved. It does mean a Stage A PASS on 2024 is a confirmation on
a season already seen, not a clean test. M8 discloses this and sets how a PASS may be read.

## Checks (all label-free)

| # | Question | Command / evidence (exit 0 each) | n | Result |
|---|---|---|---|---|
| 1 | De-vig exact; Pinnacle freshness, Stage A | `kp_probe1.py`: exactly one `snapshot_ts` per (game, kind), 0 of 2,638 t60 games with more than one, shared by all books. `kp_probe4.py`: Pinnacle `snapshot_ts − last_update` > 15 min on 0 of 418,947 matched lines. `kp_probe3.py` (real tips, `build_game_tipoff(max_season=2024)`): 0 snapshots at or after tip; max `last_update − snapshot_ts` 1.4 min. | 418,947 lines | De-vig formula PASS. Freshness holds in the data but the text does not say so (FAIL); M3 |
| 2 | Line matching; whole-number exclusion | Exclusion is stated (draft l.23-25). `kp_probe1.py`: BetRivers posts several points for 91,572 of 149,271 player-markets (ladders) and Pinnacle for 1. Conflicting duplicate prices: 1 line among the 7 books plus Pinnacle (bovada); the gate takes `max()` of the duplicates, which favours the bettor. | 149,271 | PASS on exclusion. Duplicate handling unstated (FAIL); M3 |
| 3 | Return from American odds; pushes and voids | The formula is correct (+10·a/100; +10·100/\|a\|; −10). Pushes cannot occur on half lines. Not stated: the market-to-stat column mapping, a missing box-score row, and a postponed game. | n/a | FAIL (ambiguous); M4 |
| 4 | Stage A selection | 7 books, regular season only, 2023 (`kp_probe4.py`): m=0.02: 3,822 bets / 1,011 games; 0.03: 1,085 / 557; 0.04: 301 / 201; 0.05: 89 / 63. Only 0.02 and 0.03 clear the 500 / 100 floor. Draft l.71-73 says the floor is "cleared … by m ≤ 0.04" and then that 486 is "just under", which contradicts itself. No tie-break is given. | 2023 | FAIL (text); M5 |
| 5 | BH over 7 books plus pooled | The draft does not give a CI level, RNG stream use or BH procedure. PASS needs "≥ 1 of 7 books BH-significant", but the draft's own per-book MDE is about $2 per $10, so this conjunct makes a realistic PASS almost unreachable. The draft's power claim (about 60%) ignores it. | about 200 per book | FAIL; M6 |
| 6 | Placebo information-matched? | A book's own de-vig cannot qualify a bet while its overround is > 0 (r_B > f_B always), so that version is degenerate and is no placebo. The drafted P1 (DK against FD fair, and the reverse), regular season, m=0.03: 136 bets (2023) and 406 (2024), against 1,085 and 1,215 in the primary. It is underpowered, so "P1 not voiding" is nearly automatic. A leave-one-out retail consensus (median de-vig of the other 7-book quotes at the same snapshot and point, ≥ 2 others) gives 413 / 804 bets and covers all 7 books on the same lines and timing. It is staleness-matched without Pinnacle. | see cell | FAIL; M7 |
| 7 | Clustering | Date-cluster primary and game-cluster reported is correct for a cross-game pooled mean. The same line at several books on one date stays in one cluster. Persistent per-player mispricing across dates is not covered by date clusters (BEST_PRACTICES 2026-10-20 player-season rule). | n/a | PASS; S2 |
| 8 | Survivorship | Stage A uses as-of snapshots: a line pulled after T-60 stays in, which is correct because bets stand. Lines absent at T-60 were not offered. The unmodelled risk is books voiding "obvious error" prices after the fact, which hit exactly the large-gap bets this rule selects, so a PASS is an upper bound. Stage B: non-`active` markets are excluded at entry, and unretrievable settlements are capped at 5% (copied from STAR_FLOOR M5). | n/a | PASS with disclosure; S3 |
| 9 | Snapshot timing | Stage A: `t60` stored lead 62.6-64.4 min to the real tip; `t5` is actually about 9.3 min (min 9.27). Stage B: `live_book_odds` has `received_at`, `last_update` and `snapshot_kind` (t180 … t5, close), with Kalshi filename time in naive UTC. The rule picks by `received_at` across kinds, but does not say "any snapshot_kind" or that the timestamps are UTC. | n/a | Minor FAIL; M10 |
| 10 | Holdout row for 2023/2024 settlement | 2023 and 2024 are not the holdout. T203-T206 read them with "not holdout (<=2024; 2025 never loaded)". No HOLDOUT_ACCESS_LOG row is required. Reading `player_game_stats` with `season <= 2024` in SQL is fine. The non-virgin status of 2024 (Top finding) is disclosed by M8, not by the holdout log. | n/a | PASS; M8 |
| 11 | Stage B has no verdict | The draft defines PASS/HARM/INCONCLUSIVE for Stage B (l.123-129) and a "confirmation" role (l.53-54, l.135). This contradicts Devin's decision. | n/a | FAIL; M1 |
| 12 | Open choices | Draft l.187-192 leave m, N, the BH family and the model-blend pin open. Item 4 (which model/version) is unresolved and is a free choice the harness author could make after seeing results. | n/a | FAIL; M9 |

The six attack checks (shuffled labels, planted future, missingness, knockout, replication, integer support) do not
apply before a freeze. Planted future is covered structurally: no quote is at or after the real tip, and `last_update`
is at most 1.4 min after the snapshot. Integer support does not apply because these are binary bets at a half line.

Scripts are in the scratchpad (`kp_probe1.py` … `kp_probe4.py`, run with `.venv/bin/python` from the main checkout, all
exit 0). One run was viewed through `sed -n` for line selection; its python exit code was captured from `pipestatus`.

## MUSTs: exact replacement text for `docs/prereg/KALSHI_VS_PINNACLE_PAPER.md`

**M1. Stage B is descriptive only.** Replace the bullets `Primary test`, `Horizon` and `Confirmation status` in Stage B
(l.123-129, l.135) with:
> - **No verdict (Devin, 2026-10-22).** Stage B is unpowered: its MDE is about $3.1 per $10 trade at n = 100 (about 31%
>   ROI), against a best case of about $0.31. It is therefore logged descriptively only. It has no PASS, HARM, NULL or
>   INCONCLUSIVE outcome. It carries no decision weight, does not confirm or contradict Stage A, and produces no ledger
>   verdict. It is summarised twice: on the first slate date at whose end there are >= 100 settled trades over >= 14
>   dates (if that date comes), and on the last regular-season date of 2026-27. Each summary gives:
>   - trades, dates, and distinct (player, stat, game);
>   - mean return per trade with the 95% date-cluster percentile CI (10,000 resamples, `numpy.random.default_rng(20261022)`,
>     dates sorted ascending, trades within a date sorted by (game_id, ticker));
>   - the 95% t-interval on per-date mean returns (df = dates − 1).
>
>   Each summary is labelled "descriptive, unpowered, no verdict". No interim look changes any part of this rule or the
>   decision to keep logging. A question Stage B raises gets a new rule.

Replace the Stage A bullet `Meaning for Stage B` (l.53-54) with:
> - **Stage B** is descriptive only, whatever Stage A returns. Stage A's verdict is this rule's only verdict.

**M2. Regular season.** After "Season 2025 is never loaded." (l.16) insert:
> Only games with `game_id LIKE '002%'` (regular season; play-in `005` and playoffs `004` excluded) enter selection,
> report, placebos and every slice.

**M3. Snapshot, freshness and duplicates (Stage A).** Append to the `Unit / pairing` bullet:
> Each (game_id, snapshot_kind) has exactly one `snapshot_ts`, shared by all books, so retail and Pinnacle quotes are
> taken at the same instant (verified: 0 of 2,638 t60 games have more than one). Pinnacle freshness, as in Stage B
> (N = 15): a Pinnacle line is used only if `snapshot_ts − last_update <= 15 min` for both its over and its under.
> Otherwise it is dropped and counted (0 of 418,947 matched lines today). If a (season, game_id, snapshot_kind, book,
> market, player_id, point, side) has more than one distinct `price_american`, that line is dropped at that book and
> counted; never take the max or min. A book posting several points for one player (BetRivers ladders) contributes only
> the point that exactly equals Pinnacle's.

**M4. Exact fair value, bet test and settlement.** Replace the `Fair value` and `Return` bullets with:
> - **Fair value**: for American price a, the raw implied probability is r(a) = |a|/(|a|+100) if a < 0, else
>   100/(a+100), as an exact `Fraction`. Pinnacle fair over is fo = r(po)/(r(po)+r(pu)); fair under = 1 − fo. Both sides
>   are required. The bet test uses strict `<`, evaluated in exact Fractions with m = Fraction(k, 100).
> - **Return** per $10 stake, exact: a win at a > 0 pays +10·a/100; a win at a < 0 pays +10·100/|a|; a loss is −10.
>   The stat comes from `player_game_stats` on (game_id, player_id): player_points → `pts`, player_rebounds → `reb`,
>   player_assists → `ast`, player_threes → `fg3m`. Over wins iff stat > point; under wins iff stat < point (half lines
>   only, so no push). Void, excluded and counted per book: `minutes` NULL or 0, no `player_game_stats` row for
>   (game_id, player_id), or a game with no box score.

**M5. Selection.** Replace the `m grid` bullet with:
> - **m grid (fixed)**: {0.02, 0.03, 0.04, 0.05}. **Selection (season 2023, regular season, 7 books pooled)**: an m is
>   eligible if it has >= 500 bets and >= 100 distinct game_ids with a bet. By the gate only 0.02 (3,822 bets /
>   1,011 games) and 0.03 (1,085 / 557) are eligible; 0.04 has 301 and 0.05 has 89. The selected m is the eligible m with
>   the highest pooled mean return per bet. On an exact tie, the larger m wins. If the selected m's mean is <= 0, Stage A
>   is NULL and season 2024 is not scored for return. No 2024 row is read for selection. Stage B's m = 0.03 is fixed by
>   Devin and does not depend on this selection.

Replace draft l.70-73 ("The selection floor … 0.02 or 0.03") with the sentence above giving the counts. Replace "n ≈ 1,285"
in the MDE paragraph with "n = 1,215 (7 books, regular season)".

**M6. Inference, BH and verdict order.** Replace the `Report season` and `PASS` bullets with:
> - **Report season (2024)**: at the selected m, pooled over the 7 books (a line quoted at k books is k bets). The
>   statistic is the mean return per bet. Every CI and p-value draws from a fresh `numpy.random.default_rng(20261022)`:
>   - clusters (`games.game_date`, or game_id for the game-cluster CI) are sorted ascending and drawn once with
>     `rng.integers(0, C, size=(10000, C))`;
>   - each resample's statistic is the sum of returns in the drawn clusters divided by the bets in them;
>   - the 95% CI is `numpy.percentile` at 2.5 and 97.5 (linear);
>   - the t-interval is the 95% two-sided interval on the unweighted per-date mean returns over dates with >= 1 bet
>     (df = dates − 1).
>
>   Per book: one-sided p = (resamples with mean <= 0, counting a resample with no bets as <= 0) / 10,000, over that
>   book's own dates. Benjamini-Hochberg step-up at q = 0.05 is applied over these 7 p-values. The pooled cell is the
>   primary and is not in the BH family. Per-book BH results are reported and carry no verdict weight (the per-book MDE
>   is about $2 per $10).
> - **Verdict, in this order**:
>   1. NULL if selection is NULL.
>   2. INCONCLUSIVE if there are < 300 report-season bets or < 30 dates.
>   3. PASS if the pooled mean > 0, the date-cluster percentile lower bound > 0 and the per-date t lower bound > 0, and
>      the P1 consensus placebo does not void (M7).
>   4. HARM if the date-cluster CI is wholly < 0.
>   5. NULL otherwise.

(If Devin prefers to keep "≥ 1 book BH-significant" as a PASS condition, the MDE paragraph must say that PASS then needs a
per-book effect of about 20% ROI and that power for a realistic edge is near zero.)

**M7. Placebo.** Replace the `Information-matched placebo (P1)` bullet with:
> - **P1 (staleness-matched placebo, decisive for voiding)**: same lines, snapshot, rule, m and inference, but the fair
>   value for book B is the median of the proportional de-vigs of the other 7-book quotes at the same (game, kind,
>   market, player, exact point). At least 2 other books are required; Pinnacle is not used. Gate (regular season,
>   m = 0.03): 413 bets (2023) and 804 (2024). If P1's pooled date-cluster lower bound is > 0, a PASS is voided as "edge
>   is quote staleness visible from any consensus, not Pinnacle information".
> - **P1b (reported)**: the DraftKings-against-FanDuel de-vig pair and the reverse (136 / 406 bets).
> - A book judged against its own de-vig cannot qualify (r_B > f_B whenever overround > 0), so it is not run.
> - **Positive control (reported, no decision weight)**: the same T-60 retail bets selected with Pinnacle's **t5** fair
>   value at the same point. This uses future information on purpose. If its mean is not > 0, a NULL here says the data
>   cannot show a known edge and is reported as such.

**M8. Lineage disclosure.** Append to the Lineage paragraph (l.9-11):
> Prior outcome read on both seasons: the 2026-10-12 ledger audit
> (`docs/reviews/ledger_audit_2026-10-12_part3.md`, "Following Pinnacle against DraftKings") computed the realised ROI
> of betting the DraftKings side Pinnacle favours at T-60 when the gap is >= 3 pts. The result was +0.8% [-1.1%, +2.6%]
> (n 10,015; 2023 +2.4%, 2024 -0.8%); at >= 5 pts +1.2% [-3.7%, +6.0%] (n 1,343). The 0.03 grid point and this design
> were drafted with that known. 2023 and 2024 are not the holdout, and no HOLDOUT_ACCESS_LOG row is needed; the harness
> filters `season <= 2024` in SQL and refuses any other season. A Stage A PASS is recorded as "PASS on a season
> previously read for a related rule". It supports no money claim without the forward log.

**M9. Close the open choices.** Replace the `Open choices for Devin` section with:
> ## Decided (Devin, 2026-10-22)
> m = 0.03 and N = 15 for Stage B. Stage B is tracked forward, descriptively only (M1). The Stage A BH family is the
> 7 retail books (exchanges reported only). The model-blend second fair value is removed from this rule; if wanted, it
> is a new rule with the model and version pinned before any data.

Delete "the same comparison with our model's blend … never traded on" from Stage B's `Reported` bullet.

**M10. Stage B timing.** In the `Pinnacle snapshot` bullet, after "the latest `received_at`", insert
"(any `snapshot_kind`, including `close`; `received_at`, filename time and `tipoff` all naive UTC)". In Stage A, replace
"`t5` is reported only" with "`t5` (stored lead about 9.3 min to the real tip) is reported only".

## SHOULDs
- **S1**: report the Stage A result with BetRivers removed. It carries 35% of 2023 bets at m=0.03 (415 / 1,179, playoffs
  included) and quotes ladders.
- **S2**: report a player-season-cluster CI beside the date CI (BEST_PRACTICES, 2026-10-20).
- **S3**: in Known limits, add "books void obvious-error prices after the fact; those are the large-gap bets this rule
  selects, so a PASS is an upper bound."
- **S4**: report by retail quote age (`snapshot_ts − last_update`). No retail quote among the matched lines is > 15 min
  old today, so this is a check, not a filter.

## Corrected family and power
Family: one decisive cell (pooled, report season), plus the P1 void guard. Per-book BH over 7 is reported only (M6).
Power: the MDE at n = 1,215 is about $0.80 per $10 (8% ROI, design effect 1.3). The best case at m=0.03 is about
$0.6, so power is about 55% for an edge of that size and lower for a realistic edge. A NULL means "no edge of about 8%
ROI or more". Stage B has no verdict. Recorded in TEST_LEDGER, T516 is one Stage A verdict row.

## Playbook check (market and benchmark)
1. **Information or capacity?** Information: whether Pinnacle's price carries information retail quotes lag. No
   capacity question.
2. **Enough data?** About 1.1-1.2k bets per season at m=0.03 is adequate for the pooled cell only; per-book and
   Stage B are underpowered (stated).
3. **Test validity?** Same-instant snapshots, real tips, as-of quotes and half lines are valid. The placebo is invalid
   as drafted (underpowered) and fixed by M7. A positive control is added because the expected answer is a null.
   2024 is not outcome-blind (M8).
4. **Lineage**: ODDS_HISTORY T203-T206 (market beats the model) and the 2026-10-12 Pinnacle-vs-DK ROI, which the draft
   omitted. The inherited guards are the season-<=2024 SQL filter and real tips.
5. **Scored against**: realised return at the displayed retail price (vig included), which is the right target. No
   limits or delays are modelled, so it is an upper bound.

## Single most likely failure mode if a PASS is wrong
Quote staleness or obvious-error prices visible from any consensus, not Pinnacle information. The drafted P1 is too thin
to catch this. M7 replaces it.

## Not verified
- Whether the-odds-api `last_update` marks a price change or a scrape time. Either way M3 holds, since none of the
  matched lines exceed 15 min.
- The Stage B gate counts (5 trades at m=0.03): taken as reported, not re-run.
- Whether Kalshi's settled record carries the fair-price value (carried over from STAR_FLOOR "Not verified").
- The exact gap definition used in the 2026-10-12 audit (de-vig vs de-vig, presumably). It was not re-derived.

## Drafted rows (not appended)
- TEST_LEDGER at freeze: "T516 | KALSHI_VS_PINNACLE_PAPER Stage A frozen | select 2023 / report 2024, regular season |
  not holdout (<=2024; 2025 never loaded); 2023-24 outcome-read for a related DK rule 2026-10-12 (disclosed)".
- HOLDOUT_ACCESS_LOG: none needed.
