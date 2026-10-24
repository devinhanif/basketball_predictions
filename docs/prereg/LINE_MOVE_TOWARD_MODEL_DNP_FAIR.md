# LINE_MOVE_TOWARD_MODEL_DNP_FAIR (T510b) — does the Kalshi prop price move toward our forecast, when the forecast values a DNP at Kalshi's fair price instead of "No"?

Status: **DRAFT. NOT FROZEN.** Revised 2026-10-23 after the adversary pre-freeze review (docs/reviews/t510b_prefreeze_2026-10-23.md): B1-B5 adopted verbatim, S1-S3 adopted. Drafted 2026-10-23 by the modeler at the main session's request. Forward only, paper only (no orders,
no credentials). Companion to the frozen T510 (`docs/prereg/LINE_MOVE_TOWARD_MODEL.md`, sha256 `881aca57600638f67915b5dc6a84b6a56753f026a4eae4a17811d7cee23af1ad`
at drafting time), which this text does **not** edit, re-score or replace. The main session reads it with Devin, then commits it
to `main` with the sha256 in the commit message before any harness code reads a T510b number.
Playbook classes: none fitted; the object is a stored forecast layer (gradient-boosted residual, integer-support survival grid)
compared with a market price. See the Playbook check.

## Why a second rule exists (the one-paragraph story)
T510 defines `p_M(k) = P(stat >= k | plays) x p_play`: a player who does not play counts as "No". Kalshi's own rule text for these
markets says an inactive player's market settles to a fair price, not to No. T510 is frozen and says so itself ("the harness states
Kalshi's actual rule ... and reports any market that resolved otherwise"). The T510 harness will report this; it will not change
`p_M`. T510b asks the same question with the value of the DNP branch set by the rule Kalshi publishes. It is a sensitivity
companion, not a rescue: it can add a caveat to T510 and, if it passes alone, it counts only under the stricter bar below.

## Kalshi's rule text (verified; what is and is not verified)
**Verified from the live write-once capture** (`data/raw/live_2026-10-22/kalshi_rules/KXNBAPTS/*.json.gz` and
`live_2026-10-23/...`; same for KXNBAREB, KXNBAAST, KXNBA3PT). `rules_secondary`, byte-identical in all 348 PTS, 416 REB, 309 AST and
401 3PT tickers captured on 10-22 and 10-23 (the only variation is the stat noun):

> Note: If a player is inactive, or active but never takes the court, the market settles to a fair price. Once a player enters the game the market settles based on the player's points recorded.
> ... If the game is cancelled or not started within 48 hours of its originally scheduled start, all markets will resolve to a fair price.

`rules_primary`: "If <Player> records <k>+ points in the <Away> vs <Home> Pro Basketball game originally scheduled for <date>, then the market resolves to Yes."
The June 2026 stored text (`data/kalshi/raw/kalshi-markets/hist:KXNBAPTS:first:100.json`, 100 Finals markets) said only "active but never takes the court ... last fair market
price before game start"; the live text adds "inactive". Both say **fair price**; neither says No.

**Corroborating, not the authority:** Kalshi's CFTC class certification dated 2026-10-16 for `NBAENTITYSTATCOVERED` (cftc.gov `ptc10162634013.pdf`), Appendix A, "Player
Participation" and "Entity-Specific Rules": "If a player is active but does not enter <NBA game>, all Contracts on that player ... will resolve to the last fair price, as determined by the Exchange in its sole discretion"
and "If a given <entity> ... withdraws, is removed, or is otherwise unable to participate prior to the commencement of <NBA game>, the markets on <entity> performance ... will resolve to the last fair price ...
measured as of the time immediately prior to the opening tip-off". This is the "(Covered)" product, not necessarily the KXNBAPTS series.

**Not verified:** (1) Kalshi's own contract-terms page (kalshi.com/regulatory, the kalshi-public-docs PDFs) was unreachable here (HTTP 404 on the guessed PDF path, 429 on the rulebook index); the live API text above is Kalshi's own field, but it is not the PDF.
(2) Whether KXNBAPTS is the plain or the "(Covered)" product. The Covered filing (cftc.gov `ptc10162634013.pdf`, Appendix A, "Covered settlement") reads: "If <entity> is a player and <entity> enters <NBA game> during the first half of <NBA game> but does not enter <NBA game> at any time after the conclusion of the first half of <NBA game> (including any overtime period), the Contract will resolve to the last fair price, as determined by the Exchange in its sole discretion, unless <stat> recorded by <entity> in <NBA game> satisfies the Payout Criterion, in which case the Contract will settle based on actual statistics." If KXNBAPTS is the Covered product, that branch also resolves to a fair price; neither T510 nor T510b models it. (Quoted from the filing as extracted here; not verified by the adversary, who had no copy.) (3) One third-party page (a Kawhi Leonard market aggregator) quotes "If the player is listed as inactive ... resolves No"; it is a different or older text, unverified, and
contradicted by the live field. (4) The fair price is "determined by the Exchange in its sole discretion": **the settlement price is never observed in our data** (captures show `status active, result ''`; no settled results are stored). So T510b has no PnL and no settled-price
number anywhere; the proxy L below is a modelling choice and is labelled as one.
(5) A different Kalshi product (BASKETBALLPLAYERCAT, "most <stat> among a category", CFTC `rules0623267083.docx`) does resolve a non-appearing player to No. That is a different contract and is cited only so nobody mixes the two.

## Hypothesis
H1 (as T510, restated with the fair-price branch): when `p_M'` disagrees with the Kalshi mid about 2 h before tip, the mid moves toward `p_M'` by tip-off by at least 0.010, and
more than it moves toward the recency baseline's `p_B'`, with both lower confidence bounds above zero. Null: no movement toward the model beyond noise.
What it protects against: calling the model "wrong" on a market only because T510's valuation of the DNP branch (zero) differs from Kalshi's (the fair price).

## Selection and definitions: identical to T510 except one line
Everything in T510's **Data**, **Definitions**, S0/S1/close snapshots, quote filters, one-rung-per-player-stat-game rule, the 0.05 qualification threshold, the movement definition
`m_X = (mid_close - mid1) x sign(p_X - mid0)`, and the exclusion of 2026-10-21 is copied by reference and byte-for-byte (the T510b harness must import T510's selection code, not re-implement it;
the adversary diffs the two). The single change:

- **p_M'(k)** = P(stat >= k | plays) x p_play + (1 - p_play) x **L**, with **L = mid0** (the S0 mid of the selected rung). `p_B'(k)` the same for B with B's own `p_ge_full` and `p_play`.
  Algebra: `p_M' - mid0 = p_play x (P(stat >= k | plays) - mid0)`. So the sign of the gap no longer depends on `p_play` and the size shrinks by `p_play`.
  Qualification: part 1 `|p_M' - mid0| >= 0.05`; part 2 `|p_M' - mid0| >= 0.05 OR |p_B' - mid0| >= 0.05`. `m_X` uses `sign(p_X' - mid0)`.
- **Why L = mid0 and not mid_close (the "last fair price before game start" in Kalshi's words).** The sign and the qualifier would then depend on `mid_close`, the quantity `m_X` measures: selection on the outcome. mid0 is known at S0, is the parlay engine's own proxy (`nba/parlay/joint.py`, `last_fair_price`,
  "the prediction-time mid"), and is the one choice that makes the DNP branch carry no directional claim (the model neither agrees nor disagrees with the market about a player who does not play).
  Cost: if a scratch announcement moves the fair price (the Exchange's discretion), mid0 is stale for that branch. That affects the weight `(1 - p_play)` only and is reported on the p_play < 0.9 slice.
- No DNP settlement price, payoff or PnL enters the decision. "Settlement for DNP legs that follows Kalshi's published rules" is implemented as exactly the above: the DNP branch's value is a fair price (proxied), never zero.

## Primary test (one family of two with T510; two parts, both must pass)
- Family: **{T510, T510b}**. T510 stays at its frozen 95% bounds and its frozen verdict. T510b is held to the per-test Bonferroni level for two tests (T510 itself stays at 95%, so the family is not fully Bonferroni-corrected; see the No-BH line): every "lower bound > 0" below is the **1.25th percentile** of the 10,000-resample distribution
  (a 97.5% two-sided interval) and the per-date t-interval uses the **98.75th-percentile quantile** (97.5% two-sided), df = dates - 1. The 95% bounds are also reported (comparability); they decide only the row-2/row-3 robustness reading above.
- CI method: as T510 (date-resampling with replacement, 10,000 resamples, `numpy.random.default_rng(20261022)`, dates ascending, markets within a date sorted by (game_id, ticker); pooled mean over markets in the resampled dates; part 2 resamples `m_M' - m_B'`). Dates are the cluster (cross-game movement; games on one night share news and market regime).
- **Part 1**: over part-1 qualifying markets, mean `m_M'` >= 0.010 and the 1.25th-percentile bound > 0 and the t-interval lower bound (97.5%) > 0.
- **Part 2**: over part-2 qualifying markets, mean `(m_M' - m_B')` >= 0.005 and both bounds (97.5%) > 0.
- PASS = both parts. HARM = part 1's 97.5% percentile CI wholly below 0. Else NULL.
- Floors are T510's (0.010 and 0.005 probability units), kept so the two rules are comparable; they are practical thresholds for "worth raising the model weight in the paper blend", not p-values.
- No BH: two nested near-duplicates make a BH step meaningless. T510 cannot be moved to 97.5% (frozen), so this is not a full Bonferroni: the chance that either rule falsely passes is bounded by 0.025 + 0.0125 = 0.0375 one-sided, against T510's 0.025 alone. The extra 0.0125 is the price of the row-4 path and is accepted (open choice 2).

## Horizon (fixed; no peeking-stop)
Same date set as T510: starting 2026-10-22 (inclusive), every date with a valid pre-tip capture. T510b is scored once, on the first date by which the window holds **>= 300 T510b part-1 qualifying markets AND >= 14 distinct dates**, and **never before T510 itself is scored** (if T510 is declared unscoreable, T510b is not scored; see the outcome table)
(so T510's timing is unaffected). If that date is later than T510's, the window extends with the same dates T510 would have had; a night whose capture failed is absent (logged), not imputed. Not extended or cut for any other reason.

## What each outcome means (decided here, before any movement is computed)
"T510b PASS" means at its 97.5% bounds. "T510b passes at 95%" means both parts pass with T510's own 95% bounds, computed on T510b's markets (reported for comparability; used only in rows 2–3).

| T510 | T510b | Reading |
|---|---|---|
| PASS | PASS | the model-weight proposal rests on a result that survives the corrected DNP valuation (still Devin's decision, under a new rule that picks the weight on later data) |
| PASS | not PASS, but passes at 95% | T510's proposal stands; the note says "robust to the DNP valuation at T510's level, not at the stricter companion level" |
| PASS | does not pass at 95% | T510's pass is not robust to the DNP valuation: T510's text is unedited, but the proposal is withdrawn and the note says so |
| NULL / HARM | PASS | a caveat: T510's verdict stands. T510b PASS is reported and permits one new rule to pick the weight on later data; that rule **is** T510's one permitted follow-up ("One follow-up at most"), and no other follow-up comes from either rule |
| NULL / HARM | not PASS | "the market knows more" stands; no follow-up |
| unscoreable (its dryrun fails, or it never reaches its horizon this season) | any | T510b is not scored and is reported unscoreable; no weight rule from this family |
| PASS or NULL / HARM | unscoreable (its own dryrun fails) | T510's verdict and its own "What it decides" stand unmodified; T510b reported unscoreable |

## Minimum-data gate (non-negotiable 8), run on the real live log through 2026-10-23
Script `research/scratch/gate_line_move_dnp_fair_2026_10_23.py` (movement-free: no `mid_close - mid1` is computed anywhere; run unpiped, exit 0. Not label-free: line 158 reads post-game `player_game_stats.minutes` for 10-21 and 10-22 players, used only to count DNP players for the premise check (2 of 529); no stat outcome is read, and the count decides nothing.). Sources: `nba.duckdb` `forward_predictions` (read-only; M = latest `props_context_residual` per game-player-target with `made_at <= tip - 60 min`; B = `props_recency_v1` with the same `run_id`), `data/raw/live_2026-10-{21,22,23}/kalshi/{KXNBAPTS,KXNBAREB,KXNBAAST,KXNBA3PT}_p0`.
Replication check: on the gate night 2026-10-21 the script reproduces T510's published **324** part-1 qualifiers exactly.

| night (ET date) | games with a pre-tip capture window | markets scored (after S0/S1/close filters, no fallback) | T510 part 1 | T510b part 1 | in both | only T510 | only T510b | sign flips | T510 part 2 | T510b part 2 | p_play < 0.9 scored |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10-21 (gate night, never scored) | 6 | 431 | 324 | 298 | 276 | 48 | 22 | 1 | 399 | 403 | 109 |
| 10-22 (scorable) | 2 | 98 | 57 | 53 | 49 | 8 | 4 | 0 | 81 | 84 | 19 |
| 10-23 (scorable) | 12 forecast; **0 with a pre-tip capture** | 0 | 0 | 0 | | | | | | | |

Per stat, T510b part 1 over 10-21 + 10-22: pts 95, reb 83, ast 83, fg3m 90.
Over the two nights T510 has 381 part-1 qualifiers and T510b 351; 325 are in both (85% of T510's, 93% of T510b's). The rules disagree on 82 of the 407 markets in the union (20%): 56 only T510, 26 only T510b, 1 sign flip. The disagreement is concentrated where p_play < 0.9 (T510 qualifies 104 of 128 such markets, T510b 82).

Gate checks against the rule's own requirements:
| check | requirement | measured on the real log | pass |
|---|---|---|---|
| the rule text this rule relies on is captured | live `rules_secondary` for every ticker used | 348 / 416 / 309 / 401 tickers (PTS/REB/AST/3PT) on 10-22..23, identical wording; 10-21 rules not needed (night excluded) | PASS |
| T510 selection reproduced | 324 on 10-21 | 324 | PASS |
| enough qualifiers for the 300 minimum | rate x nights | 44 T510b part-1 per game pooled (351 over 8 games with a pre-tip window; the 10-22 night alone 27); 300 needs about 7-11 games, i.e. 2-3 normal slates | PASS (not binding) |
| enough distinct dates (binding) | 14 valid dates | **1 valid scorable date so far (10-22)**. 10-23 is lost: the live capture directory for 10-23 starts at 2026-10-24T02:45:29Z, after the last 10-23 tip (02:00Z); the 12-game slate has no pre-tip snapshot. There are no games on ET 2026-11-03 (`data/schedule/live_tips_2026-27.parquet`), so if every night from 10-24 captures, the 14th valid date is **2026-11-06**; each further capture failure moves it one game night. The live Kalshi capture is started by hand and is not in launchd (DECISIONS 2026-10-23 21:45); every night needs a start before the first tip − 150 min. | PASS, with 13 more valid nights needed |
| premise observable (descriptive; does not decide) | at least 5 DNP-resolved markets by the scoring date to check Kalshi's "fair price" against what resolves | 2 of 529 scored markets (0.4%) belong to a player with no box minutes (10-21: 2; 10-22: 0). At that rate the window will hold about 6-10 | WEAK: expected to be marginally verifiable |

Capture audit before any "fail" (non-negotiable 8; no gate fails, but the 10-23 hole is a data gap): data we hold for 10-23: forecasts (12 games, made 12:00:21Z), Kalshi snapshots only from 02:45Z 10-24 (after all tips), ESPN and rules snapshots on 10-23 evening; nothing earlier. Repairable only by a different source (Kalshi's public candlestick history for those 13 games' markets, free, read-only), which would define quotes differently from T510's file-timestamp snapshots. Cost: a market-agent pull and a rule line. **Default: do not repair**; absent date, logged, as T510 does.

## Premise check (reported, never decides)
Once the window's games are final, for the markets in the scored set whose player has no box minutes: record the Kalshi settlement (public read-only market endpoint, `result` and `settlement_value`) and report n DNP-resolved, n resolved at a price strictly between 0 and 1 (fair), n resolved No.
Pre-registered reading: if n >= 5 and a majority resolve exactly No, T510b's valuation is contradicted by the exchange and the note says T510b's premise failed (T510's construction was right in practice); if n < 5, "unverified, settlement price unobserved". The decision table above is unchanged by this. If the reading is 'premise failed', a row-3 withdrawal (outcome table) is reported as resting on a contradicted premise; the withdrawal still stands.

## Reported, not part of the decision
Per stat; by gap size (0.05-0.10, > 0.10); toward / away / flat counts; a sign-shuffled placebo; the three sets {both, only T510, only T510b} separately (mean `m_M` and `m_M'` on each; this is the cleanest view of what the DNP valuation changes);
drops by reason and by `sign(p_M' - mid0)`; the p_play < 0.9 slice and a p_play >= 0.9 slice; mean `mid_close` for markets whose player was later inactive (descriptive: does the market price a scratch toward 0 or toward the old mid, i.e. which settlement rule traders believe).

## Harness requirements before the scoring date
Written after the freeze, importing T510's selection code. Before it reads any real mid, it passes T510's synthetic dryrun (L-S2) under the same noise, with p_cond and p_play drawn independently of the quote noise and p_M′, p_B′ built from them and the synthetic mid0 exactly as above: (i) T510b false-pass rate <= 2.5% over 1,000 draws at the 97.5% bounds (T510's 5% tolerance is for a 2.5% one-sided level; this rule's level is 1.25%); (ii) a planted +0.02 move toward M′ passes. If (i) fails, T510b is reported unscoreable, not run. The adversary diffs the selection against T510's and checks the 97.5% bounds.
No statistic of movement toward M, M′, B or B′ on any night from 2026-10-22 is computed or read before this rule's freeze commit. Disclosed: the MARKET_SURFER_PAPER result (cd46134, read 2026-10-23 before this freeze) reported a pooled, momentum-conditioned mid-to-mid drift (+0.96c) over 10-21 and 10-22 across all Kalshi NBA series, including 97 KXNBAPTS trades. It uses no forecast and does not condition on any sign of p − mid0.

## Pending priors (modeler, 2026-10-23, written before any movement was computed; also in docs/PREDICTIONS_LEDGER.md)
P(T510b PASS) 0.08. P(part 1 alone passes) 0.16. P(T510b verdict differs from T510's) 0.12 (they share 85-93% of qualifiers and the same nights, forecasts and mids). P(the 14th valid date is 2026-11-06) 0.15 (restated before freeze: 11-05 was impossible, with no games on ET 11-03); P(by 2026-11-12) 0.75.
P(>= 300 T510b part-1 qualifiers by the 14th valid date) 0.97. P(>= 5 DNP-resolved markets observed by then) 0.55; P(a majority of those resolve No | >= 5 observed) 0.12.
Expected mean `m_M'` +0.001 (80% -0.009 to +0.012); expected `m_M' - m_B'` about 0.000 (80% -0.006 to +0.006).

## Playbook check (docs/MODEL_PLAYBOOK.md; classes: quantile and distribution layers, gradient-boosted trees)
1. **Information or capacity?** No new model and no new feature. The change is a valuation of the unavailable branch from the exchange's published settlement text. It adds no pre-tip information; its value is removing a mis-specified comparator.
2. **Enough data for the class?** Nothing is fitted. The data question is dates (14) and markets (300); the gate above says dates bind and one scorable night is in hand.
3. **Test validity.** Real tip times from `forward_predictions`; snapshots by capture file timestamp; L = mid0 so selection does not depend on the movement being measured; the T510 dryrun guards the stale-quote and reverting-noise cases; the 97.5% bounds price the second look at the same movements. A test that cannot reach 14 dates is unscoreable, not a null; the gate says whether that is at risk (it is, by capture reliability).
   Weaknesses stated: the near-duplicate is not independent evidence (shared markets, shared nights, shared M, B and mid0); the proxy for the fair price is a modelling choice; Covered-vs-plain settlement is unverified.
4. **Lineage.** Descends from T510 (adversary MUSTs L1-L5 inherited by import) and from the parlay engine's `last_fair_price` policy. Guards carried: one rung per player-stat-game, no fallback, two-sided quote filter, date clustering, sign-shuffled placebo. Dropped: nothing. Added: 97.5% bounds on T510b (per-test Bonferroni; family false-pass bound 0.0375), the premise check.
5. **Scored against?** The Kalshi mid's own later movement, a price-direction test: it asks whether the model leads the market, not whether it is calibrated at a line (CRPS gains here have not reached the line, T477). It cannot say anything about a bet's EV.
Class checks. Quantile layer: the reliability that matters is at the posted rung, which `p_ge_full` is read at directly; the integer grid is respected (`c[k-1]/n`); `p_play` is a single scalar applied to all rungs, so the DNP branch's weight is the same for pts, reb, ast and 3PM. Trees: the M-vs-B contrast isolates the context residual (both share `p_play`); the missingness check is the S0/S1/close drop table by `sign(p_M' - mid0)`.

## Open choices for Devin (defaults stated; none changes the family or the floors)
1. L = mid0 (default) vs L = mid at S1 vs a pre-registered "L = p_cond" (rejected: makes the DNP branch agree with the model by construction).
2. Multiplicity: 97.5% bounds on T510b (default; per-test Bonferroni, family false-pass bound 0.0375 one-sided), or hold T510b at 95% and call it descriptive-only.
3. Repair the 10-23 hole from Kalshi candlesticks: default no.
4. Settle-price premise check: default descriptive and automated by the market agent after each night's games are final.
5. Whether the main session wants T510b registered at all, given 85-93% overlap and P(PASS) 0.08. Claude's view: cheap (one harness flag, no new data) and it closes an advance-disclosed hole in T510; do it.

## Known limits
- M as T510 defines it (latest run with made_at <= tip − 60); from 10-23 some games' M is a late refresh, not the morning run. Report the slice by M age (tip − made_at <= 3 h vs > 3 h). (This is T510's limit text too; T510 is frozen, so it is noted there, not edited.) A late scratch is a separate branch: after the player is announced out, the market's own price movement may reflect the exchange's settlement discretion rather than information; the p_play < 0.9 slice and the inactive-player `mid_close` line show it.
- The fair price is the Exchange's sole discretion; no settlement price is stored. T510b's proxy is not a measurement.
- 10-21 contributes to the gate, not to the score; one scorable night so far.
