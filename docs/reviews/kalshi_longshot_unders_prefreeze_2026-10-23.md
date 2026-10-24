# Pre-freeze review: KALSHI_LONGSHOT_FORWARD and UNDERS_FORWARD (sibling family), 2026-10-23

Reviewer: adversary. Written 2026-10-23 22:46 CDT (from `date`).
Drafts reviewed:
- KALSHI_LONGSHOT_FORWARD: `docs/KALSHI_LONGSHOT_FORWARD.md` at 00c1516 (`agent/market-kalshi-longshot-fwd`).
- UNDERS_FORWARD: `docs/UNDERS_FORWARD.md` at 0477e4e (`agent/market-unders-forward`). This is Devin's override and is not approved for freeze.

Data: a copy of `data/kalshi/kalshi.duckdb` taken 2026-10-24T03:34Z (`kalshi_prices` 1,193,117 rows), plus the raw pages under
`data/kalshi/raw/2026-10-*/`. Season-2024 outcome correlations come from `data/backups/nba_20261023.duckdb`. Every connection was
`read_only=True`, and the backup was queried with `season = 2024` only. Season 2025 was not read. No net return, fee-adjusted payoff or
price-versus-outcome calibration was computed. The disclosures at the end list what outcome information I did read.

Verdict: BLOCKED

**KALSHI_LONGSHOT_FORWARD: NOT_READY. UNDERS_FORWARD: NOT_READY.** All required fixes are text-only: no new data, code or fit is
needed. With the replacement texts below adopted verbatim, both are READY_TO_FREEZE under the defaults, subject to Devin's fee check.
UNDERS also needs Devin's freeze approval.

Top finding: **excluding unsettled contracts one at a time selects on the outcome.** Kalshi closes and settles a YES on a "k or more"
contract the moment the stat is reached ("This market will close and expire early if the event occurs"). A NO settles only after the
final. On the 2026-10-23 slate at 03:34Z, 904 of 1,644 eligible prop contracts were settled, and the unsettled ones are mostly NOs. Both
drafts exclude unsettled contracts one by one, with a read cutoff of "settlements of 2027-01-14". Any game still settling at the read
would therefore enter YES-enriched, which pushes the NO-side `R` down. That same exclusion also drops late-settling DNP scalars (up to
13.75 h). Fix: exclude games as whole units and lag the read by 2 days (M2).

Second finding: **the fee that decides the verdict is read at run time from a config that other branches edit.** `configs/parlay.yaml`
already says `verified: true` (source "user-provided ... 2026-10-08"), so the "refuses unless verified" gate passes today without
anyone checking anything. The Bayes-Kelly branch touches the same file (DECISIONS 2026-10-23 23:0x). A coefficient or multiplier edit
after a result would change `R`. Fix: pin the fee in the frozen text (M3).

Third finding: **the family is not coherent yet.** LONGSHOT at 0.01/0.04 plus UNDERS at 0.005/0.02 gives a family-wise one-sided error
of 0.075, not 0.05. LONGSHOT's KXNBASPREAD tails, 368 of 1,798 eligible contracts (20%), are also the trade in SPREAD_TAIL_NO Part B
(`agent/market-spread-tail-no`, 0.01/0.04, BH m = 3). That rule is not in anyone's family (M11).

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none fitted. Both rules are market-calibration tests of a fixed trade: buy NO at `1 - yes_bid` at the entry snapshot and hold
to settlement. The nearest playbook classes are "Quantile and distribution layers", because the question is reliability at the point that
matters (here the price), and "Set and joint models", because the binding issue is power under same-game dependence.
1. **Information or capacity?** Neither. The only input is the T-60 price. UNDERS's strata use our forecast and Pinnacle's line as labels
   and select nothing. Both drafts say so.
2. **Enough data for this class?** Nothing is fitted. The binding quantity is **dates**, not games or contracts (M9): 70 ET dates exist
   from 2026-10-25 to 2027-01-13, and the rule needs 50.
3. **Test validity.** Tipping is real: 28 of 28 regular-season games on 10-20..23 have `occurrence − 180` equal to a scheduled tip. DNPs are
   scored at Kalshi's fair price, read correctly from raw (check 3). Support is native (x.5 strikes on integer stats). Clustering is by game
   and by date. Three invalidities need fixing before freeze: the outcome-dependent unsettled exclusion (M2), float arithmetic on the
   filters (M1), and UNDERS's shuffle self-test, which cannot move (M10). Per BEST_PRACTICES 2026-10-21, a null needs a positive control.
   LONGSHOT has none and UNDERS's control is inert, so neither could record NO_POSITIVE_EV_FOUND validly as drafted.
4. **Lineage.** UNDERS descends from LONGSHOT and from MARKET_SURFER_PAPER's hold-to-settlement arm. LONGSHOT shares the KXNBASPREAD tails
   with SPREAD_TAIL_NO Part B. Neither draft carries that overlap into the multiplicity (M11). LONGSHOT has **no Playbook check section**
   (L1). The inherited guards (entry snapshot, DNP at fair price, verified fee) are carried forward.
5. **What it scores against.** Realised net dollars per contract against a market-is-fair null that includes costs. That is the right
   object for "is it worth money" (BEST_PRACTICES 2026-10-11: the decision score is the headline). It is not a Brier comparison with the
   price.

## Checks requested

| # | Check | Command / evidence | Result | Pass |
|---|---|---|---|---|
| 1 | Selection on outcome: 240-min entry window | `attack/universe.py` (rebuilds every market's entry snapshot from `kalshi_prices` live rows; `python -I attack/universe.py kalshi_copy.duckdb meta.parquet live_tips_2026-27.parquet univ.parquet; echo exit=$?` → exit=0) | Entry is the latest `ts <= tip-60` with age <= 240. Exclusion depends on the capture clock only. UNDERS eligible entries have median age 10 min and max 213.9 min. Nothing outcome-dependent enters. A stale entry is still a price that was available at `ts`, so it does not leak; it does dilute the meaning of "T-60" (S2 reports it) | PASS |
| 2 | Selection on outcome: unsettled exclusion | `attack/settle.py univ_entries.parquet` (exit 0) | ET 10-23, eligible props: 904 of 1,644 settled at 03:34Z. Hours from tip to settlement: YES median 2.80 (10th pct 1.86, min 0.33), NO 2.96 (10th pct 2.63), scalar median 3.11 (max 13.75). Contract-level exclusion of unsettled markets keeps early YESes and drops pending NOs | **FAIL** (M2) |
| 3 | Selection on outcome: spread <= 5c, bid >= 1c, bucket edges | same script | The filters use only the entry snapshot. **But the stored prices are FLOAT (float32)**: `yes_ask - yes_bid <= 0.05` in float rejects **258 of 499** contracts whose spread is exactly 5c. UNDERS eligible: integer cents 4,462, float 4,217. LONGSHOT eligible: integer 1,798, float 1,763. The siblings' own audits disagree. LONGSHOT's 1,456 matches float (KXNBASPREAD 301 settled, exact). UNDERS's 4,462 matches integer exactly. Which contracts drop depends on how a price pair rounds in binary (not on the outcome), but the frozen universe is undefined until the arithmetic is fixed. UNDERS's U8 `(0.85, 0.99]` leaves out a possible mid of 0.995 (bid 99, ask 100); there are 0 now | **FAIL** (M1, S3) |
| 4 | DNP / scalar settlement read from raw | `attack/meta.py` over 13,574 raw page files; `settle.py` | 27 regular-season scalars, 27 of 27 with `settlement_value_dollars` (0.05 to 0.92). 26 have `expiration_value` "Confirmed DNP"; 1 has none (KXNBAREB-26OCT21INDNOP-INDANESMITH23-2, 0.82, settled 13:45Z the next day). So `n_dnp` must count `result = scalar`, not the label (S4). yes/no carry 1.0000 / 0.0000 (2,816 / 4,615). The DB result equals the raw result for 7,458 of 7,458. `rules_secondary` covers "inactive, or active but never takes the court" verbatim. Only 3 (UNDERS) and 1 (LONGSHOT) of the 27 scalars pass the entry filters, so DNP dilution is small | PASS |
| 5 | Fee through FeeModel, refuses unless verified | `nba/parlay/config.py:15-69, 96-108`; `configs/parlay.yaml:2-15` (sha256 f25f263c46a4e7de…) | `FeeModel` has no refusal; `load_config` returns `verified` as read. The config already says `verified: true` with source "user-provided … 2026-10-08", so the refusal is vacuous today. It is read at run time and is shared with other branches (Bayes-Kelly touches it). The formula `0.07·M·P(1−P)` applied to `P_no = 1 − bid` is correct and symmetric. "Unrounded primary, per-contract round-up as S1" is consistent with the config's per-ORDER round-up | **FAIL** (M3) |
| 6 | Clustering by game and date | Draft text; `attack/deff2.py` | Game and ET-date percentile bootstraps, wider one decides: correct direction. Missing: the bootstrap statistic must be stated as a ratio of sums (S5). Missing: a player-level concentration report (BEST_PRACTICES 2026-10-20; S6). `nba/truth/bootstrap.py` does not exist (`ls nba/truth` → `__init__.py metrics.py walkforward.py`), so each scorer would carry its own CI code (minor; S5) | PASS with S5/S6 |
| 7 | Multiplicity and overlap | Draft §7 of each; `git show agent/market-spread-tail-no:docs/prereg/SPREAD_TAIL_NO.md` lines 140-160 | Bonferroni over two rules and two looks (0.005 + 0.02 per rule, 0.05 total) is valid under any dependence. LONGSHOT's 0.01/0.04 breaks it: the total is 0.075. UNDERS line 51 freezes a stale fact about LONGSHOT. UNDERS line 9 makes UNDERS's text depend on a draft that will change. SPREAD_TAIL_NO Part B tests NO on KXNBASPREAD tails at 0.01/0.04, and LONGSHOT includes 368 such contracts. The LONGSHOT/UNDERS verdict pairs are logically coherent: both can hold because the populations differ. They also share every long-shot prop contract, and the scorer should assert the shared per-contract `r_no` are identical (S7) | **FAIL** (M11) |
| 8 | Verdicts pre-decided, no escape hatch | LONGSHOT lines 101-108, 124-128; UNDERS lines 127-133, 155-163 | (a) "or `R` is clearly negative" is undefined; it lets a judgement call reach NO_POSITIVE_EV_FOUND with an upper bound >= 0.010. (b) There is no mapping for KEEP_SHADOWING or INSUFFICIENT_SAMPLE at the final read, which invites a Read 3. (c) Guard 8.3 "blocks the look until repaired" moves the look. (d) Guard 8.2's "carry the whole sign" has no definition and no consequence. (e) UNDERS 8.6 self-tests have no stated consequence | **FAIL** (M4, M6, M7, M10) |
| 9 | Minimum-data gate on real data (non-negotiable 8) | `live_tips_2026-27.parquet` (sha256 9702c830abe987ff…; 1,200 games, no team columns); projection script in "Commands" | Games ET 10-25..01-13: **538 on 70 dates**. Through 04-12: 1,164 on 151. The drafts project games only. Capture failures come as whole nights (the Mac asleep on 10-23), so **dates bind**: 50 dates by Read 1 needs at least 72% of nights captured (71% gives 01-14, 60% gives **2027-01-27**). Both drafts say the gate "passes with margin at Read 1 if capture is at least 60% reliable", and that is false. LONGSHOT's "23 / 28 = 0.82 game fraction" is a settlement artefact: 28 of 28 games had an eligible long shot, and the 5 missing were 10-23 games not yet settled. Counts gate: PASS at >= 72% night capture. The text is wrong | PASS on substance; text wrong (M9) |
| 10 | Power claims (assumed ICC) | `attack/deff.py` (ladder nesting only, label-free); `attack/hist_corr.py`, `attack/tail_de.py` (season 2024); `attack/deff2.py` (Gaussian copula over all pairs) | Measured below. The drafts assume design effects of 13.4 (LONGSHOT) and 10.5 (UNDERS). Exact ladder nesting from the entry mids alone gives 2.2 / 1.9. Adding season-2024 same-player cross-stat and cross-player correlations gives **3.7-3.9 / 3.9-6.2**. The drafts' SEs are conservative by about 1.5-2x. LONGSHOT's MDE is also computed at one-sided 0.05, while its look is at 0.01 (0.005 after M11) | Power is better than claimed; text wrong (M12) |
| 11 | Disclosed glances | DECISIONS 2026-10-23 22:14 ("a gross bucket glance on 13 games … preceded filter choices"); LONGSHOT lines 175-180; UNDERS lines 234-241 | LONGSHOT's disclosure omits the order that DECISIONS records (L2). Effect on validity: none for type I. Every choice made after the glance is tested on games from 10-25 onward that nobody has seen, and only one variant is frozen. The cost is power and prior calibration, not false passes. UNDERS's 37.1% combined with its own bucket table (mean entry mid about 0.39 by bucket midpoints) is a crude gross-calibration read of about 2c, and the draft should say so (S8). Neither glance chose the side: NO is the favourite-longshot hypothesis and Devin's idea | PASS with L2, S8 |
| 12 | Paper-Kelly column | UNDERS lines 167-174 | `f = (q_lo − c)/(1 − c)` is the right Kelly for a binary contract bought at `c`, evaluated at a pessimistic payoff quantile. Defects: (a) it sits **outside** the frozen section, so its definition (10th percentile, cells) can be changed after results; (b) it is printed per bucket and per stratum, a many-cell maximum with no multiplicity; (c) about 159 same-game contracts are correlated (design effect 4-6, check 10), so per-contract fractions do not add up to a portfolio stake, and the column does not say so; (d) DNP payoffs are not binary (minor) | **FAIL** (U2) |
| 13 | Playbook check | LONGSHOT: none; UNDERS lines 196-212 | LONGSHOT has no section. UNDERS answers all five questions | LONGSHOT **FAIL** (L1); UNDERS PASS |
| 14 | Window and identity | LONGSHOT line 32-37; UNDERS 62-66; schedule file | "Joined by game" cannot be executed: the schedule file has only `gameId`, `gameDateTimeUTC` and `gameDateTimeEst`, with no teams. LONGSHOT says Cup knockout games are "not in the window of any read below", but they are in December. They are absent from the file: no games ET 2026-12-04..11; 1,200 of 1,230 games are listed. UNDERS's z stratum needs a Kalshi ticker → player_id map, which is present on 865 of 4,462 eligible contracts (19%) | **FAIL** (M8); S9 |
| 15 | Scope | `git diff --stat main...<branch>` | Each branch has the draft plus one PREDICTIONS_LEDGER row. Nothing under `nba/` or `configs/` | PASS |

The six standard attacks (shuffled labels, planted future, missingness, knockout, replication, integer support) need a result or a
harness, and neither exists. The harness review must run these: a planted post-T-60 snapshot that the entry selection must ignore; the
missingness table (no_entry, wide_spread, bid_zero, unsettled_game, scalar) by series, bucket and date; leave-one-series-out and
leave-one-date-out; and the calibrated-null coverage self-test (M10). Integer support holds by construction.

### Measured design effect (label-free ladder part; season-2024 cross-ladder part)

Rows: the 10-20..23 eligible entries (UNDERS 4,462 contracts, about 159 per game; LONGSHOT 1,798, about 64 per game). The null is a
calibrated market, so each contract is a YES with probability equal to its entry mid. Within a ladder the events are nested, so
`Cov = min(p_a, p_b) − p_a p_b` exactly. Other pairs use a Gaussian copula with season-2024 latent correlations. Same player, cross-stat:
pts-fg3m 0.69, pts-reb 0.34, reb-ast 0.27, others 0.05-0.23. Player vs game totals: 0.01-0.15. Total vs team total: 0.78. Margin vs team
total: ±0.63. Cross-player latent `rho_x` is a sensitivity. The empirical cross-player indicator ICC on 2024 is **0.011** at both the
0.92 and 0.70 quantiles (`tail_de.py`). That is a latent value of about 0.02-0.03, so the 0.03 row is the realistic upper case.

| Rule | DE (drafts) | DE ladder only | DE full, rho_x 0 / 0.015 / 0.03 | SE at 300 games (drafts → measured) | MDE80 at Read 1, alpha 0.005 | P(PASS-bound) at true +1c, Read 1 |
|---|---|---|---|---|---|---|
| UNDERS | 10.5 | 1.9 | 3.9 / 5.0 / 6.2 | 0.0066 → 0.0037-0.0047 | 1.3-1.6c (drafted 2.3c) | 0.33-0.54 |
| LONGSHOT | 13.4 | 2.2 | 3.7 / 3.8 / 3.9 | 0.0073 → 0.0038-0.0039 | 1.3c (drafted 2.0c, at the wrong alpha) | 0.50-0.53 |

At Read 2 (700-900 games, alpha 0.02) the MDE80 is 0.6-0.9c for both, and power at +1c is 0.88-1.00. Caveats: the Gaussian copula
understates tail co-movement (blowouts), which is why the 0.03 row exists; equal-weight pooling and real non-calibration change the
variance slightly. The realised cluster variance still replaces all of this at each look, as both drafts say. The drafts' power claims are
**conservative**, so the counts gate is not threatened by power. The text should still state the measured values (M12).

## MUSTs (replacement text; adopt verbatim)

Severity: M2, M3, M4, M8, M10, M11 are blockers. M1, M5, M6, M7, M9, M12, L1, L2, U1, U2 are major.

**M1 (both; arithmetic).** LONGSHOT line 45-46 and UNDERS line 72-73: append to item 6:
> All price comparisons, buckets and returns use integer cents: `bid_c = round(100 * yes_bid)`, `ask_c = round(100 * yes_ask)` from the
> stored value; eligibility is `bid_c >= 1` and `ask_c - bid_c <= 5`[LONGSHOT only: `and bid_c + ask_c <= 30`]; `mid = (bid_c + ask_c) / 200`;
> `P_no = (100 - bid_c) / 100`, `P_yes = ask_c / 100`. The stored FLOAT columns are never compared directly (on the 2026-10-20..23 capture a
> float comparison rejects 258 of the 499 contracts whose spread is exactly 5c).

**M2 (both; selection on outcome).** LONGSHOT line 87, first sentence ("Not yet settled by the read date: excluded and counted
(`unsettled`).") and UNDERS line 120 ("Unsettled by the read date: excluded and counted."): replace with:
> A look scores only games whose ET tip date is at least 2 days before the read date (Read 1: tips on or before 2027-01-13; Read 2: on or
> before 2027-04-12). A game with any eligible contract unsettled at the read is excluded WHOLE and counted (`unsettled_game`), never
> contract by contract: Kalshi settles a YES on a "k or more" market as soon as it is reached and a NO only after the final (2026-10-23
> slate at 03:34Z: 904 of 1,644 eligible contracts settled; YES median 2.8 h after tip, NO 3.0 h; DNP scalars up to 13.75 h), so a
> contract-level exclusion would keep YESes and drop NOs.

And in §7 of each (LONGSHOT line 117, UNDERS line 145): replace "(data through settlements of 2027-01-14)" / "(settlements through
2027-01-14)" with "(games with ET tip dates through 2027-01-13; Section 5/6 game-level settlement rule)".

**M3 (both; pinned fee).** LONGSHOT §3 and UNDERS §5: append:
> The fee parameters are pinned by this text: taker coefficient 0.07, formula `p_one_minus_p`, multiplier M = 1.0 for every series in
> Section 1.7 (Devin confirms these against Kalshi's published schedule before the freeze commit; if his check differs, these numbers are
> edited before the freeze commit). The scorer REFUSES to run if `verified` is not true, or if the loaded coefficient, `default_multiplier`
> or `fee_multiplier` give a different fee for any series in this universe than the pinned values. The one exception is a Kalshi schedule
> change recorded in `configs/parlay.yaml` with its Kalshi source URL and effective date before the read date. Such a change applies only
> to entries on or after its effective date. A config edit for any other reason never changes this rule's fee. Negative tests: the scorer
> refuses on `verified: false` and on a changed coefficient.

**M4 (both; no escape hatch).** LONGSHOT line 105-106: delete "Also reported if `R` is clearly negative." UNDERS line 131: delete ", or `R`
is clearly negative". In each, after the KEEP_SHADOWING bullet, insert:
> - **Final read.** At Read 2, KEEP_SHADOWING is recorded as **INCONCLUSIVE** and the rule closes; INSUFFICIENT_SAMPLE at Read 2 also
>   closes it. There is no Read 3, no extension, and no re-read with later settlements. A point estimate below zero is reported with its CI
>   and is never by itself a verdict.

**M5 (both; guard 8.1 denominator).** LONGSHOT line 124 and UNDERS line 155: replace "fewer than 70% of otherwise-eligible settled
contracts have an entry snapshot" with:
> fewer than 70% of the settled markets of this rule's series on in-window games have a `source='live'` row with `ts <= tip − 60 min` and
> age <= 240 min (price filters are not applied to this denominator; they need the snapshot)

**M6 (both; concentration guard defined).** LONGSHOT line 126-127 and UNDERS line 157-158: replace the series/stat clause with:
> PASS additionally requires that `R` recomputed leaving out each series [UNDERS: each stat] in turn stays > 0 (point estimate). If any
> leave-one-out `R` is <= 0, the verdict is KEEP_SHADOWING at Read 1 and INCONCLUSIVE at Read 2.

**M7 (both; no moved look).** LONGSHOT line 128 and UNDERS line 159: replace "blocks the look until repaired" / "block the look" with:
> make the look INSUFFICIENT_SAMPLE (settlement) unless the missing values are fetched from Kalshi's market endpoint within 7 days of the
> read date; the look's game cutoff never moves.

**M8 (both; window and tip join).** LONGSHOT line 32-38 and UNDERS line 62-66: replace "Playoffs/Cup knockout excluded (not in the window
of any read below)." / "Playoffs and Cup knockout excluded." and the Tip-time item with:
> The window is the set of games in `data/schedule/live_tips_2026-27.parquet` as committed at freeze (sha256 written in the FREEZE RECORD;
> 1,200 games). The Cup knockout games and the Cup-week games scheduled later (no games ET 2026-12-04..11 are in the file) are excluded and
> counted; playoffs and play-in are after Read 2. The file has no team columns, so a Kalshi game is in the window iff its
> `occurrence_datetime − 180 min` equals a scheduled tip in that file on the same ET date (28 of 28 regular-season games 2026-10-20..23
> matched); tip = that scheduled time. No match: excluded and counted (`no_schedule_match`). ET dates use `America/New_York`.

**M9 (both; gate text, dates bind).** LONGSHOT line 170-172 (from "The n gate (300 games, 50 dates)" to "fails Read 1 ... worse than
that.") and UNDERS line 228-229 (from "The 300-game and 50-date gate passes" to "at least 60% reliable,"): replace with:
> Dates bind, not games: the schedule has 538 games on 70 ET dates from 2026-10-25 to 2027-01-13 (1,164 on 151 dates through 2027-04-12).
> Capture failures come as whole nights, so 50 dates by Read 1 needs at least 72% of nights captured. At 100% both counts are met on
> 2026-12-23, at 80% on 2027-01-06, at 71% on 2027-01-14, and at 60% only on 2027-01-27, which is an INSUFFICIENT_SAMPLE Read 1. Every
> scheduled game 2026-10-20..23 had at least one eligible contract (LONGSHOT: 28 of 28; the earlier "23 / 28" counted only games already
> settled).

**M10 (UNDERS 8.6 replaced; LONGSHOT gains it as 8.6).** UNDERS line 162-164: replace guard 6 with this text, and add the same text to
LONGSHOT §8 as item 6:
> 6. Scorer self-tests run before every look and are printed. Any failure voids that look's verdict: the report prints SELFTEST_FAILED and
>    the counts only, until the scorer is fixed under an adversary-reviewed code change. The rule text never changes. (a) The Section 4
>    identity on every contract, in integer hundredths of a cent. (b) Calibrated-null coverage: K = 200 synthetic outcome sets on the
>    look's own contracts. Each ladder (one player-stat, one team's spread or team total, one game total) gets one uniform draw u, and a
>    rung is YES iff u < its entry mid, so ladders stay nested. DNPs are not simulated. The game and date structure is the real one. The
>    decision-CI one-sided 5% bound must exclude the known true `E[r_no]` in 2% to 9% of sets. (c) Planted edge: the same sets with every
>    YES probability multiplied by 0.80 (long shots overpriced). The point estimate must fall within 0.5c of the known true `R`, and the
>    decision CI must cover it.
> (The drafted within-game outcome shuffle is removed: it leaves every game's sum of `y`, and therefore `R`, unchanged, so its rejection
> rate depends on the real outcome rather than on the CI procedure.)

**M11 (family).** LONGSHOT line 117-120: replace "One-sided level 0.01 at Read 1 and 0.04 at Read 2 (the decision CI is the matching 98% or
92% two-sided percentile interval)" with "One-sided level 0.005 at Read 1 and 0.02 at Read 2 (the decision CI is the matching 99% or 96%
two-sided percentile interval)". Replace "Family size m = 1 (the pooled NO-side return). No BH is needed; slices are descriptive." with:
> Family: this rule and UNDERS_FORWARD test overlapping contracts on the same capture, under Bonferroni across the two rules and both looks
> (each rule 0.005 + 0.02; family one-sided 0.05). These levels hold whether or not UNDERS_FORWARD is ever frozen and are never relaxed.
> Within this rule m = 1 (pooled `R`); slices are descriptive.

LONGSHOT line 48 (item 7, Kinds): replace "`game` = KXNBAGAME/SPREAD/TOTAL/TEAMTOTAL." with:
> `game` = KXNBAGAME/TOTAL/TEAMTOTAL. KXNBASPREAD is excluded from the decision pool because its tails are SPREAD_TAIL_NO Part B's trade
> (separately frozen, own levels); it is reported as a descriptive slice with the overlap count.

(Alternative, if Devin prefers to keep the spread tails: Bonferroni over three rules, 0.0033/0.0133 each, written into all three. Default:
exclude. It costs about 20% of LONGSHOT's contracts and no measurable power: the ladder-only design effect is 1.7 without the spread
tails against 2.2 with them, at the same effective contracts per game.)
UNDERS line 51: replace the LONGSHOT cell "0.01 / 0.04, m = 1" with "0.005 / 0.02 (Bonferroni, same family)". UNDERS line 149-150: replace
"LONGSHOT's draft states 0.01/0.04 (m = 1) and must be amended to 0.005/0.02 by whoever freezes it, in the same commit or before;" with
"LONGSHOT is frozen (or will be) at the same 0.005/0.02;".

**M12 (both; power text).** LONGSHOT line 109-115 (the Power paragraph) and UNDERS line 137-142: replace with:
> Power (measured on 2026-10-20..23 entry mids, label-free, plus season-2024 within-game correlations; the realised cluster variance
> replaces it at each look): design effect about [LONGSHOT 3.8 | UNDERS 4-6]. Exact ladder nesting contributes [2.2 | 1.9]; the rest is
> same-player cross-stat and cross-player co-movement. SE of `R` at 300 games about [0.0038 | 0.0037-0.0047]. MDE at 80% power: about
> 1.3-1.6c at Read 1 (one-sided 0.005), 0.6-0.9c at Read 2 (one-sided 0.02, 700-900 games). Power to clear the bound at a true +1c: about
> 0.33-0.54 at Read 1 and 0.88-1.00 at Read 2. The Gaussian copula understates tail co-movement, so these are optimistic by an unknown
> margin; the earlier assumed ICC (0.2 / 0.06) overstated the design effect about 2-3x.

**L1 (LONGSHOT; playbook).** After "## Sensitivities", insert a "## Playbook check (docs/MODEL_PLAYBOOK.md)" section. Use UNDERS lines
196-212 with three changes: "player props" becomes "props, stat-sum combos and game totals"; under Lineage, "Shares contracts with
UNDERS_FORWARD (long-shot band) and formerly with SPREAD_TAIL_NO (KXNBASPREAD, now excluded from the decision)"; and under Test validity,
cite M2, M8 and M10.

**L2 (LONGSHOT; disclosure order).** Line 179-180: replace "The rule was written before any net-of-cost return was computed." with:
> The rule was written before any net-of-cost return was computed, but AFTER this glance: the 15c cap, the bucket edges and the 240-min
> entry age were chosen with the glance already seen (DECISIONS 2026-10-23 22:14). The scoring window starts 2026-10-25 and contains none
> of these games, so the glance cannot raise the false-pass rate. It can only have shaped the prior and the power.

**U1 (UNDERS; self-contained).** Line 9-11: replace "Everything not restated below is that draft's text and numbers (entry snapshot, fee
object, DNP settlement, clustering, guards, looks). The differences are listed in Section 0 so a reader needs only this file to score it."
with:
> This file is self-contained: the frozen section below is the whole rule. LONGSHOT is cited for lineage only; later edits to it do not
> change this rule.

**U2 (UNDERS; paper Kelly).** Move §9 inside the frozen section, unchanged except for this. Replace "Shown per bucket and stratum" with
"Shown per bucket only (never per stratum; a bucket with fewer than 30 games prints nothing)". Append:
> The fraction is per single contract in isolation. About 160 same-game contracts are correlated (design effect 4-6), so fractions never
> add up to a stake, and no number here enters a verdict.

The alternative is Devin's open choice 8: drop the column. Either is fine. Leaving it outside the frozen section is not.

## SHOULDs (adopt or answer before freeze)

- **S3 (UNDERS line 80):** "U8 (0.85,0.99]" → "U8 (0.85,1.00)".
- **S4 (both):** "`n_dnp` counts `result = scalar` whatever `expiration_value` says (1 of 27 scalars on 10-20..23 had no label)."
- **S5 (both):** define the statistic as a ratio of sums: each bootstrap replicate resamples whole clusters and computes
  `sum r_no / sum n`. Report the Monte Carlo SE of the bound (with B = 10,000, the one-sided 0.005 tail rests on 50 draws). Put the cluster
  bootstrap in `nba/truth/bootstrap.py` and use it for both rules.
- **S6 (both):** add to guard 2: "the share of `R` carried by the five players [LONGSHOT: five players or teams] with the most contracts,
  and `R` without them" (BEST_PRACTICES 2026-10-20).
- **S7 (both):** the scorer asserts that the per-contract `r_no` on tickers scored by both rules is identical.
- **S8 (UNDERS line 238):** append "Combined with this audit's bucket table (mean entry mid about 0.39 by bucket midpoints), the 37.1% is a
  crude gross read of about 2c below the price; it is disclosed as such."
- **S9 (UNDERS §3):** state where `prop_predictions` is read (which DuckDB file), and that the ticker → player_id map is the one in force
  at the read. Only 865 of 4,462 eligible contracts (19%) carry `player_id` today, so `unknown` will dominate the z and Pinnacle axes until
  the alias fix lands. The headline cell "z > +0.5 and D = 0" will be small.
- **S10 (LONGSHOT PREDICTIONS_LEDGER row):** after M11, restate P(PASS) at 0.005/0.02, before freeze, and leave the earlier row unchanged.
- **S11 (both):** the date clustering has about 70 clusters at Read 1. The percentile bootstrap is mildly anti-conservative at
  one-sided 0.005 with that few clusters. The "wider of the two" rule partly covers this. State it in the report.

## Corrected family and power note

Family F = {KALSHI_LONGSHOT_FORWARD, UNDERS_FORWARD}, Bonferroni across the two rules and both looks: one-sided 0.005 at Read 1 and 0.02 at
Read 2 per rule, family 0.05. SPREAD_TAIL_NO Part B stays outside F only if LONGSHOT drops KXNBASPREAD from its decision pool (M11).
Otherwise F has three members at 0.0033/0.0133. Power: measured MDE at 80% is about 1.3-1.6c at Read 1 and 0.6-0.9c at Read 2, on a +1c
floor. A Read 1 "bound includes 0" means "no power below about 1.5c", not "no edge".

## The single most likely failure mode if a PASS is wrong

A PASS on NO-side returns is most easily faked by which contracts are scored. As drafted, that is outcome-dependent settlement exclusion at
the read cutoff (M2). It is followed by a fee read from a mutable config (M3).

## What was not verified

- Kalshi's current published fee schedule and any NBA-series multiplier (no network; this is Devin's check).
- Whether Kalshi halts or delists a player's markets before tip when he is ruled out. This affects how stale an entry can be, not
  validity.
- The cross-player tail co-movement beyond the season-2024 indicator ICC (0.011). The Gaussian copula may understate blowout tails.
- That `kalshi_prices` writes a row for every open ticker at every snapshot rather than only on change. Entry ages (median 10 min, max
  213.9) are consistent with every snapshot, but I did not read the writer.
- The SPREAD_TAIL_NO draft beyond its Part B levels and universe.

## Disclosures (outcome information the reviewer read)

- Settlement status and timing of 2026-10-20..23 contracts, all outside both windows: counts of yes 2,816 / no 4,615 / scalar 27 among
  markets with an entry snapshot, settlement hours by result, and the 27 scalar values. No price was compared with an outcome, and no
  return was computed.
- Season-2024 player box scores (`season = 2024` filter) for correlation structure only.
- No season-2025 row was read.

## Commands (scratch, not in the repo; all `read_only=True`; exits shown unpiped)

Scratch dir: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/214e8eaf-cfca-4a90-ae80-a0349b39bd8b/scratchpad/`
- `cp data/kalshi/kalshi.duckdb <scratch>/kalshi_copy.duckdb` → exit=0 (2026-10-24T03:34Z)
- `python -I attack/meta.py data/kalshi/raw <scratch>/meta.parquet` → 14,259 tickers, exit=0
- `python -I attack/universe.py kalshi_copy.duckdb meta.parquet data/schedule/live_tips_2026-27.parquet univ.parquet` → exit=0
- `python -I attack/settle.py univ_entries.parquet` → exit=0
- `python -I attack/deff.py univ_entries.parquet` → exit=0; `python -I attack/deff2.py univ_entries.parquet 0.0,0.015,0.03` → exit=0
- `python -I attack/hist_corr.py data/backups/nba_20261023.duckdb` → 26,061 rows, 1,230 games, season 2024, exit=0
- `python -I attack/tail_de.py data/backups/nba_20261023.duckdb` → exit=0
- Schedule projection one-liner (games and dates by night-capture rate) → exit=0

## Proposed rows (drafted, not appended)

AGENT_INCIDENTS (review block, substantive): `| 2026-10-23 | adversary (block) | review: substantive | KALSHI_LONGSHOT_FORWARD +
UNDERS_FORWARD M1-M12: contract-level unsettled exclusion selects YES (outcome), fee read from a mutable config, alpha family 0.075 with a
SPREAD_TAIL_NO overlap, inert shuffle self-test, no final-read mapping, float32 filter boundary (258 of 499 at exactly 5c), dates-bound gate
misstated | pre-freeze review | one revision round | text fixes before freeze |`

DECISIONS (for the main session, after Devin): `| 2026-10-23 2x:xx | LONGSHOT/UNDERS pre-freeze review BLOCKED (docs/reviews/
kalshi_longshot_unders_prefreeze_2026-10-23.md): adopt M1-M12, L1-L2, U1-U2 verbatim; LONGSHOT drops KXNBASPREAD from its decision pool
(SPREAD_TAIL_NO owns it) and moves to 0.005/0.02. | Family coherence; selection on settlement timing. | Devin prefers the three-rule
Bonferroni. |`

---

## Re-review (diff only), 2026-10-23 22:53 CDT (from `date`)

Diffs read: UNDERS `git diff fb873ea 2ac7812` on `agent/market-unders-forward` (docs/UNDERS_FORWARD.md and one appended
PREDICTIONS_LEDGER row). LONGSHOT `git diff 00c1516 2e70ffb` on `agent/market-kalshi-longshot-fwd` (docs/KALSHI_LONGSHOT_FORWARD.md and
one appended PREDICTIONS_LEDGER row). No new attacks and no new data reads. The only checks were for new contradictions introduced by
the revision.

Verdict: BLOCKED

**UNDERS_FORWARD: READY_TO_FREEZE**, provided the two one-line edits R-U1 and R-U2 below go into the freeze commit (cosmetic, no further
review), plus the fee-confirmation step F1 and Devin's freeze approval. **KALSHI_LONGSHOT_FORWARD: NOT_READY.** Its frozen power text
carries UNDERS's numbers (my M12 template's fault), and its guard 8.6(a) cites an identity that its Section 4 never states. With R-L1 to
R-L5 adopted verbatim it is READY_TO_FREEZE. No further adversary look is needed; the main session can confirm the edits by diff.

### What was checked and held

| Item | Both | Note |
|---|---|---|
| M1-M11 adopted verbatim inside the frozen markers | yes | M11 default taken: LONGSHOT at 0.005/0.02 with KXNBASPREAD descriptive. UNDERS §0 alpha cell updated |
| M10 shuffle removed; SELFTEST_FAILED consequence present | yes | |
| U1 self-contained; U2 Kelly inside FROZEN, per bucket only, correlation sentence | yes (UNDERS) | `<!-- FROZEN-END -->` now follows §9 |
| L1 Playbook check (market agent's text) | adequate (LONGSHOT) | All five questions answered, lineage includes the SPREAD_TAIL_NO change. Accepted as written |
| L2 glance order disclosed | yes (LONGSHOT) | |
| S3: U8 (0.85, 1.00] | yes (UNDERS) | Correct. With integer cents the maximum mid is 0.995, so U8 now covers every eligible mid |
| UNDERS text outside the frozen section (Playbook, power, gate, open choice 3) | consistent | Except the 0.82 parenthetical (R-U2) |
| Ledger rows | appended, earlier rows unchanged | Both restate P before freeze (S10 met for LONGSHOT) |
| M3 placeholders "TO BE CONFIRMED BY DEVIN AT FREEZE" | present in both, inside the frozen section | Handled by F1 |

### Freeze-time step (both)

**F1.** In the freeze commit, before hashing, replace the paragraph beginning "TO BE CONFIRMED BY DEVIN AT FREEZE" with:
> Confirmed by Devin on <YYYY-MM-DD> against <Kalshi fee-schedule URL>: taker coefficient <value>, M = <value> for every series in
> Section 1.7 [UNDERS: Section 1.7], formula P(1-P).

If any confirmed value differs, edit the pinned numbers in the next paragraph in the same commit. A rule hashed with "UNCONFIRMED" in it
is not frozen under M3.

### Remaining edits, UNDERS (cosmetic; adopt in the freeze commit)

**R-U1 (frozen, line 48, Section 0 table).** Replace the LONGSHOT cell "props, combos, game markets" with "props, combos, game markets
except KXNBASPREAD (descriptive only)". Line 172: replace "Section 5/6 game-level settlement rule" with "Section 6 game-level settlement
rule".

**R-U2 (outside the frozen section, line 263-264).** Replace "(the LONGSHOT draft's 0.82 was for contracts at mid <= 15c; with all
prices a game always has contracts)" with "(LONGSHOT's earlier 0.82 counted only games already settled; every game also had an eligible
long shot)".

### Remaining edits, LONGSHOT (R-L1 and R-L2 are frozen text: required)

**R-L1 (frozen, Section 6 Power paragraph).** Replace the whole paragraph from "Power (measured on 2026-10-20..23" to "about 2-3x."
with:
> Power (measured on 2026-10-20..23 entry mids, label-free, plus season-2024 within-game correlations; the realised cluster variance
> replaces it at each look): design effect about 3.8 with KXNBASPREAD included. Exact ladder nesting contributes 2.2 with it and 1.7
> without it, at the same effective contracts per game (about 29). The rest is same-player cross-stat and cross-player co-movement. SE
> of `R` at 300 games about 0.0038. MDE at 80% power: about 1.3c at Read 1 (one-sided 0.005) and about 0.6-0.7c at Read 2 (one-sided
> 0.02, 700-900 games). Power to clear the bound at a true +1c: about 0.50-0.53 at Read 1 and 0.97-0.99 at Read 2. The Gaussian copula
> understates tail co-movement, so these are optimistic by an unknown margin; the earlier assumed ICC of 0.2 overstated the design effect
> about 3.5x.

**R-L2 (frozen, Section 4).** After "The spread is therefore inside every return: the NO buyer pays `1 - bid`, the YES buyer pays `ask`."
(line 91), insert:
> Integrity identity the scorer asserts on every contract (guard 8.6(a)), in integer hundredths of a cent:
> `r_yes + r_no = -(ask_c - bid_c) / 100 - fee(P_yes) - fee(P_no)`.

Without this sentence, guard 8.6(a) checks an identity the rule never defines, so the self-test cannot be implemented as written, and
every look would void on SELFTEST_FAILED or depend on an identity chosen by whoever writes the harness.

**R-L3 (frozen, Section 7).** Replace "Section 5/6 game-level settlement rule" with "Section 5 game-level settlement rule".

**R-L4 (audit, outside the frozen section; the gate text must not contradict itself, non-negotiable 8).**
- Line 195-196: replace "(of 28 scheduled games 2026-10-20..23; 23 games have at least one eligible long shot at max age 240)" with
  "(of 28 scheduled games 2026-10-20..23; all 28 have at least one eligible long shot at max age 240, and 23 of them were fully settled
  at the audit time)".
- Line 204, after the row's last cell, add a row:
  `| Decision pool after M11 (KXNBASPREAD out), integer cents, max age 240 | 1,430 contracts, 28 games (about 51 per game; 368 KXNBASPREAD contracts reported descriptively) | Rate source after the revision |`
- Line 214-215: replace "23 / 28 = 0.82 (and the capture was broken on parts of 10-20 and 10-23, so this is not an upper bound).
  Cumulative eligible games reach:" with "28 / 28 = 1.0 (the earlier 23 / 28 counted only games already settled). Games are not the
  binding count (dates are, below); for reference, cumulative games reach:".
- Line 220: replace "0.8 (observed 0.82)" with "0.8".
- Line 223: delete "By 2027-01-15 (Read 1) the 0.6 case has about 330 games; by the end of the regular season the 0.6 case has well
  over 700. " (a 0.6 game fraction says nothing about the 0.6 night case, which is INSUFFICIENT_SAMPLE at Read 1).

**R-L5 (audit, line 228).** Replace "power is no longer the binding limit" with "power at Read 1 is about 0.5 at a true +1c (Section 6),
so a Read 1 bound that includes 0 means no power below about 1.3c, not no edge".

The audit table's by-series row (line 205, SPREAD 301) may stay as a historical count; the added row under R-L4 states the post-M11
pool. The 1,430 / 368 figures come from this review's integer-cent rebuild (check 3 above: 1,798 eligible, of which 368 KXNBASPREAD,
including the then-unsettled 10-23 games).

### New contradictions found by this re-review

1. LONGSHOT power paragraph: the UNDERS MDE (1.3-1.6c) and power (0.33-0.54) ranges were attached to LONGSHOT. The cause was the
   reviewer's bracketed M12 template, which gave per-rule values only for the design effect and SE. Fixed by R-L1.
2. LONGSHOT guard 8.6(a) has no matching identity in Section 4. Fixed by R-L2.
3. "Section 5/6" in both §7s. Fixed by R-U1 and R-L3.
4. LONGSHOT's audit still says 23 / 28 = 0.82 and pairs a 0.6 game fraction with "330 games" at Read 1. The new dates text right after it
   says 60% night capture fails Read 1. Fixed by R-L4.
5. UNDERS §0 still describes LONGSHOT's series as including all game markets. Fixed by R-U1.
