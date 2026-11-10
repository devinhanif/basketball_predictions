# BEST_PRICE_FORWARD: adversary pre-freeze review (2026-11-09)

Reviewed: branch `agent/modeler-best-price-forward`, commit `e4a9f73`; rule text `docs/prereg/BEST_PRICE_FORWARD.md` rev 0
(sha256 above `=== RESULTS BELOW ===` re-computed: `888e2d44bb215ffa5113f35b2f2ac47ffa59a1fd2c6696ac33ce0d20cad5d107`, matches the
request); gate `research/eval/best_price_forward_gate.py`; `reports/prereg_best_price_forward/gate.json`;
`tests/research/eval/test_best_price_forward_gate.py`; the DECISIONS and PREDICTIONS_LEDGER rows in the same commit.

Verdict: BLOCKED

Pre-freeze word: **NOT_READY**. Six MUST edits (below, as exact replacement text). None of them is a leak in the data captured so far;
two would make the rule unable to reach a decision word (M1, M3), one leaves a planted-future hole open in the code (M2), one breaks a
standing BEST_PRACTICES rule on clustering (M4), one is a label collision that puts a sensitivity inside the BH family (M5), one makes the
gate and the future bootstrap non-reproducible (M6). After the edits the text hash changes; the gate must be re-run and this review
re-done on the revision (re-review checks the whole revision, not only these fixes).

**Top finding.** Self-test 10.9(a) requires the one-sided lower bound to exceed the known true ROI in "2% to 9% of" 200 sets. A perfectly
calibrated CI exceeds at the look's own level: 0.5% at Read 1, 2% at Read 2. Exact binomial: the band is met with probability **0.019** at
Read 1 and **0.569** at Read 2 when the CI is exactly right. Because "any failure voids the look's word", Read 1 can almost never issue
PASS or NO_POSITIVE_EV_FOUND, and Read 2 voids 43% of the time on a correct bootstrap.

No outcome-linked number was computed. No `forward_scores`, box-score stat, Kalshi `result` or settled page was read; season 2025 was
never loaded; every DuckDB connection was `read_only=True`. Attack scripts are in the scratchpad
(`/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f9498c6-47e0-4634-a07a-43f2643fe309/scratchpad/bpf/`).

## Playbook check (docs/MODEL_PLAYBOOK.md)
Model class: no new model. A betting-strategy evaluation (execution-cost question) of the production props model
(`props_context_residual`, GBM context residual over recency averages) passed through the frozen LINE_CALIBRATION map.
1. **Information or capacity?** Neither; the lever is price (cost). Venue prices enter the pick only through the consensus median
   (verified in code, `build_picks` l.442-454: side depends on `p_cal` and `p_cons` only; best venue chosen after).
2. **Enough data for this class?** Nothing is fitted. Gate counts re-derived exactly (2,129 CAL picks, 135 games, 19 dates). Power to say
   "no" is real at Read 2; power to say "yes" exists only for an edge of about +3-4 points, and less once player-persistent noise is
   modelled (M4) and the second-best-price guard is counted (S7).
3. **Is the test valid?** Mostly. Invalidities found: self-test band (M1), undefined word precedence (M3), missing player clustering
   (M4). Known-at for model rows and Kalshi rows is enforced and tested; for book rows it is enforced by the data (one `t60` pull per
   game) but not by the code (M2).
4. **Lineage and inherited guards.** T489/T491/T492 lineage correct; whole-game settlement exclusion and fee pin inherited from
   KALSHI_LONGSHOT_FORWARD. Not inherited and should be: the 2026-10-20 BEST_PRACTICES rule on per-player clustering and player-shared
   noise in power (M4); the 2026-10-22 information-fair-baseline listing (S6).
5. **Scored against?** Realised money per unit at the executable price after fees; the null is the market (ROI < 0). Correct target for
   the question.

## Checks run

| # | check | command (scratch) | n | result | pass |
|---|---|---|---|---|---|
| 0 | rule hash | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/BEST_PRICE_FORWARD.md \| shasum -a 256` | 1 | 888e2d44..., matches | PASS |
| 1 | gate reproduces | `python -W ignore -m research.eval.best_price_forward_gate --data-root /Users/devin/Downloads/nba-prediction --until 2026-11-08 --out .../bpf/gate_rerun.json` (exit 0, 4.7 s) | 2,129 picks | every count field identical; `power_cal_arm` differs (expected_roi -0.0322 vs committed -0.0324; per_game_sd 0.2617 vs 0.2647) | FAIL (M6) |
| 2 | planted future: model row E+5 min | existing test `test_predictions_refuse_rows_made_after_entry` | 1 | refused | PASS |
| 2 | planted future: Kalshi row E+1 min, row > 20 min old | existing test `test_kalshi_entry_refuses_post_entry_and_stale_quotes` | 1 | refused | PASS |
| 2 | planted future: book row labelled `t60`, received E+50 min, price +250 vs -110 | `bpf/plant.py` (in-memory DuckDB, exit 0) | 1 | **accepted**: `load_book_quotes` returned both rows; `build_picks` keeps the last row per (book, side, point), so the planted price can win | FAIL (M2) |
| 2 | real data: more than one `t60` pull per game? | `bpf/q1.py` (read-only, exit 0) | 135 games | 1 distinct `received_at` per game in all 135; span 0 s | PASS (data), hole is in code |
| 2 | conflicting duplicate prices within `t60`, U1 books, main markets | `bpf/q2.py` (exit 0) | 0 keys | none (the 1,402 duplicates seen are other markets) | PASS |
| 2 | `close` snapshot timing (used in attack 10.4) | `bpf/q1.py` | 127 games | received 0.07-4.8 min before tip: pre-tip, after E; refusal must come from `received_at <= E`, not from tip | note (M2) |
| 3 | missingness / void asymmetry | rule text sections 4, 7 | n/a | books void, Kalshi scalar counts, unsettled game excluded whole; absent-from-box players not named explicitly | minor (S8) |
| 4 | knockout | n/a (no features; venue LOO is attack 10.7) | | | n/a |
| 5 | replication | n/a pre-freeze (forward-only; no outcomes) | | | n/a |
| 6 | integer support | x.5 points only, whole numbers excluded (rule 5.3; code l.410) | | no push grid | PASS |
| 7 | Kalshi threshold semantics | `bpf/q3.py` (title column only) | 3 markets | threshold 20 = "20+ points" → over 19.5, as coded `thr - 0.5` | PASS |
| 8 | self-test band 10.9(a) reachable | `scipy.stats.binom`, K = 200 | exact | P(band met \| calibrated) = 0.019 (Read 1), 0.569 (Read 2) | FAIL (M1) |
| 9 | word reachability / exclusivity | section 8 read against section 10 | | PASS vs NO_POSITIVE_EV_FOUND exclusive (PASS needs point >= 0.010, so upper >= 0.010); SELFTEST_FAILED has no place in the word list and no Read 2 resolution | FAIL (M3) |
| 10 | player persistence of picks | `bpf/wc.py` (label-free, exit 0) | 2,129 picks, 185 players | median 11 picks per player in 19 dates; players with >= 5 picks take the same side 80.2% of the time; 81% of picks sit on a (game, player) with >= 2 picks | FAIL vs BEST_PRACTICES 2026-10-20 (M4) |
| 11 | winner's curse, label-free | `bpf/wc.py` | 2,129 | best minus second-best net decimal: median 0.034, p90 0.100, share > 0.10 = 10.9%; E[ROI \| consensus true] at best -0.0325, at second-best -0.0539 | guards adequate (S7 note) |
| 12 | Kalshi spread at entry | `bpf/wc.py` | 1,072 Kalshi-quoted picks | median 1c, p95 4c, >= 10c 0.4% | PASS (mid in consensus is sound) |
| 13 | gate script scratch dependency | `grep`/`ls` for `inspect.py` in worktree root and `research/eval`; imports read | | imports only `nba.parlay.line_cal`, `nba.parlay.top_source`, `nba.datamanifest`; no scratch path, no `inspect` | PASS |
| 14 | label-free gate (no outcome read) | code read, all SQL | | no `forward_scores`, no `result`/`settled_ts`, no box score; `season = 2026` in every SQL touching `games` | PASS |
| 15 | verification | `.venv-1/bin/python -m pytest -q tests/research/eval/test_best_price_forward_gate.py` | 11 tests | "11 passed"; exit 1 from the repo-wide 70% coverage floor on a single-file run | PASS (tests) |
| 15 | | `ruff check` / `ruff format --check` / `mypy` on the gate (and test) | | exit 0 / 0 ("2 files already formatted") / 0 | PASS |

## Attack findings by topic

**Winner's curse and stale quotes.** The side is chosen from `p_cal - p_cons` only; the best price is chosen after (verified). The
`last_update` filter is inert, as disclosed. The 0.07 consensus filter is loose next to a 0.03 pick threshold: a venue may disagree with the
median by up to 7 probability points and still set the price; for an under pick the best price is, by construction, at the venue with the
highest P(over), i.e. the venue most against the pick. The guards carry the protection: 10.3(a) second-best ROI >= 0 (label-free, this
costs about 2.1 points: -0.0325 vs -0.0539 under consensus truth), 10.3(b) drop gaps > 0.10 (10.9% of picks), 10.3(c) executability at
the next snapshot (conservative: survivors are picks whose price held or improved, which is the market moving against the pick). Adequate
for a PASS guard; add the deviation-bin descriptive (S11).

**Known-at.** Model rows `made_at <= E` and Kalshi `ts <= E` are enforced in SQL and tested. Book rows are selected by `snapshot_kind`
only; today every game has exactly one `t60` pull so nothing leaks, but a retry or relabel would. Attack 10.4 replaces prices with `close`
rows, which are pre-tip and after E; only a `received_at <= E` filter refuses them (M2).

**DNP and Kalshi scalar.** Books void, Kalshi scalar counts at `settlement_value_dollars`, unsettled games excluded whole, box/Kalshi
disagreement > 1% voids: sound. The best-price comparison treats a book void and a Kalshi scalar as equal-risk; this is a property of the
contract and legitimately part of ROI. S1's paired difference is therefore >= 0 on every pick except Kalshi-best DNPs (S1 finding).

**Maker arm.** Order at E one cent inside, filled only if a later pre-tip snapshot shows the ask through the order by 1c; fee 0.0175 P(1-P)
charged. 15-minute snapshots miss intra-interval trade-throughs (fewer fills: conservative); fills select on adverse moves (stated);
paper only. Acceptable as a secondary. The config says Kalshi fees are rounded up to the cent per order; the primary uses the unrounded
fee (same as the sibling). At a $1 order this is up to 1 point of ROI on Kalshi-priced picks (13% of picks); add a descriptive (S5).

**Family and multiplicity.** Own family, m = 1, 0.005 + 0.02 over looks; project-wide union with KALSHI_LONGSHOT_FORWARD and
UNDERS_FORWARD <= 0.075 stated. The union bound holds under any dependence (the rules overlap on Kalshi under contracts and this rule is 72%
unders). LINE_CAL_LIVE asks a different question (calibration of the same probability, no money) and is correctly not merged. Secondary
family: S1 is mechanically non-negative (S1 below), which makes BH over {S1, S2, S3} more lenient for S2/S3 (thresholds 2q/3 and q instead
of q/2 and q). Name collision: section 2.3 calls the outside-U1 venue report "S3", which is the maker arm in the BH family (M5).

**Power.** Re-derived from the gate's own SDs (independent synthetic outcomes): at Read 1, P(NO_POSITIVE_EV_FOUND | market right) is about
0.62 at 331 games and 0.76 at 414; the text's "about 0.5" is conservative. With a player-persistent shift of SD 0.05 the player-clustered
SE on the gate picks rises from 0.0225 to 0.0241 at 19 dates; over a season (about 65 picks per player) the design effect is roughly 1.6
and Read 1 power falls to about 0.4-0.6. Read 2 stays above 0.9 at 80% capture. PASS power: needs a true best-price ROI of about +0.04
(Read 1) / +0.03 (Read 2), and 10.3(a) pushes the effective bar up by about 2 points. This must be stated (M4 re-run, S7).

**Decision words.** PASS and NO_POSITIVE_EV_FOUND are mutually exclusive. SELFTEST_FAILED (10.8) and "voids the look's word" (10.9) are not
in the word list, have no order relative to the others, and at Read 2 would leave the rule open forever ("until explained"). Fixed by M3.

**Guard set on PASS.** Complete for the known attacks (shuffle, second-best, gap, executability, planted future, best week, venue count,
venue LOO, settlement, self-tests, concentration, stat LOO). Missing: player concentration (M4) and an exact number for "far above the
floor" (S3).

**Window cleanliness.** Clean. Every scored game is after the freeze date; the disclosed views (scorecard side hit rates 10-20..28, JARVIS
record 36-92, scorecard Brier gap +0.0095) are outcome summaries of earlier dates, which is the select-then-report pattern the project
uses; they moved priors, and the text states the parameters were fixed before them. Nobody can have seen an outcome in the window. The
preseason Kalshi row is outside every window. The one post-gate definition change (stale median) was a code fix with identical pick
counts. No reason to move the window.

**Gate script dependency.** The disclosed `inspect.py` import happened in a scratch counting script, not the gate. The gate imports only
repo modules; run as `python -m` from the worktree root, `sys.path[0]` is the worktree root, which contains no `inspect.py`. Clean.

**Overs-only slice (Devin's venue).** Yes, pre-declare it as descriptive. On the gate, 602 of 2,129 CAL picks are overs (28%, 126 games);
projected about 4,600 over picks by Read 2 at full capture. Declaring it now prevents a post-hoc "but the overs made money" slice from
being mined later; it earns no word and sits in no family (S4 text).

**Venues.** "Has everything" does not change the default: the six US books plus Kalshi stand. Polymarket stays outside U1 because the
vendor label cannot be tied to Polymarket US, whose fee is the verified one; that is a data fact, not an account fact. If Devin's
"everything" means he cannot use some of the six books, S2 (Kalshi) is the money-relevant arm, as the text already says.

## MUST edits (exact replacement text)

**M1. Section 10.9(a)** (line 169-170). Replace "(a) K = 200 synthetic outcome sets drawn from `p_cons` (market right) on the look's own
picks and clustering: the decision-CI one-sided lower bound exceeds the known true ROI in 2% to 9% of sets;" with:

> (a) K = 1,000 synthetic outcome sets drawn from `p_cons` (market right) on the look's own picks, rows and clustering, with outcomes
> correlated within (game, player): latent `z = sqrt(0.3) u_(game,player) + sqrt(0.7) e`, `over = 1{Phi(z) < p_cons}`, the pick wins iff
> its side matches; plus a player-persistent shift `N(0, 0.03)` added to every `p_cons` of that player in the set (clipped to [0.01,
> 0.99]), the known true ROI of a set being its expectation under the shifted probabilities. The decision-CI one-sided lower bound exceeds
> the known true ROI in at most 1.5% of sets at Read 1 (nominal 0.5%) and at most 3.5% at Read 2 (nominal 2%), and the one-sided upper
> bound falls below it in at most the same shares. A CI that is too wide is not a failure. `numpy.random.default_rng(2027)`.

Also in 10.9(b) replace "over the 200 sets" with "over the 1,000 sets" and add the same within-(game, player) correlation.

**M2. Section 4.2** (line 69). Replace "2. **Sportsbook quotes**: the `t60` rows of that game only." with:

> 2. **Sportsbook quotes**: the `t60` rows of that game with `received_at <= E` only; a row labelled `t60` with `received_at > E` is
> refused and counted (`post_entry_quote`). Where a (book, side, point) has more than one admissible row, the latest `received_at`, then
> the latest `last_update`, then the worse price for the bettor is used. A planted `t60` row stamped E + 1 min with a better price must
> leave the pick and its price unchanged (unit test).

Code (owner applies): `load_book_quotes` joins the per-game E and filters `received_at <= E`, ordering duplicates as above instead of
"last row in fetch order"; add the test in `bpf/plant.py` form to `tests/research/eval/test_best_price_forward_gate.py`.

**M3. Section 8**, after the KEEP_SHADOWING bullet (line 137), add:

> - **Order of evaluation (exactly one word per look).** (1) `t60` coverage below 70% (10.1) or the minimum not met → INSUFFICIENT_SAMPLE.
>   (2) Settlement agreement (10.8) or a self-test (10.9) fails → SELFTEST_FAILED, no direction claimed: at Read 1 the rule continues to
>   Read 2; at Read 2 one code-only fix, logged in docs/DECISIONS.md before the re-run, and one re-run on the same rows are allowed, and a
>   second failure is INCONCLUSIVE. (3) PASS conditions met → PASS. (4) NO_POSITIVE_EV_FOUND conditions met → NO_POSITIVE_EV_FOUND.
>   (5) Otherwise KEEP_SHADOWING at Read 1 and INCONCLUSIVE at Read 2. A nominal pass that fails any guard or attack in section 10 falls
>   to (5). The rule closes at Read 2 whatever the word.

and in 10.9 replace "(printed; any failure voids the look's word, the rule text never changes)" with "(printed; a failure gives
SELFTEST_FAILED under section 8's order; the rule text never changes)".

**M4. Section 8 CI bullet** (line 127-128). Replace "clusters = whole games, and separately whole ET dates; the **decision CI is the
wider** of the two. Both printed." with:

> clusters = whole games, separately whole ET dates, and separately players (all of a player's picks in the look); the **decision CI is
> the widest** of the three. All three printed, with the share of total profit carried by the top five players (BEST_PRACTICES
> 2026-10-20: picks repeat the same player on the same side, 80% of a player's picks on the gate capture).

In 10.10 append: "and ROI > 0 (point) leaving out the five players with the most picks." In section 11 replace the power table and the
Read 1 sentence with a re-run that includes the M1 synthetic correlation and the player clustering (gate code change, owner applies), and
re-state P(NO_POSITIVE_EV_FOUND at Read 1 | market right) from that run.

**M5. Section 2.3** (line 50). Replace "(reported in sensitivity S3, never in a decision)" with "(reported in descriptive arm S6, never in a
decision and never in the S1-S3 family)". Section 11 table (line 198): replace "S3 / open choice 2" with "S6 / open choice 2". In section
6 add: "- **S5 RAW (descriptive)** and **S6 outside-U1 venues (descriptive)**: ROI with CI, no word, no family."

**M6. Section 8 CI bullet**, append: "Before any random draw, picks are sorted by (game_date, game_id, player_id, stat) and clusters are
indexed in sorted key order; the gate's synthetic draws use the same order, so the gate and every look reproduce bit for bit." Code
(owner applies): sort `cal_picks` and `raw_picks` before `synthetic_power` and `summarise`; regenerate `gate.json` and quote its numbers.

## SHOULD edits

- **S1. Secondary family** (line 142-144). Replace "BH at q = 0.10 over three one-sided tests (S1: paired ROI difference > 0; S2, S3: ROI
  > 0)" with "BH at q = 0.10 over two one-sided tests (S2, S3: ROI > 0); S1's paired difference is reported with its CI but is not tested
  (best price >= DraftKings on every pick, so the difference is non-negative by construction except on Kalshi-priced DNPs)".
- **S2. Shuffled picks (10.2)** append: "Every kept venue quotes both sides at L, so the flipped side has the same venues; the shuffle
  uses `default_rng(2028)`."
- **S3. Planted outcome (10.4)**: replace "far above the floor" with "at least +0.30".
- **S4. Overs-only slice.** Add to section 6: "- **D_OVER (descriptive, pre-declared)**: the primary's over picks only, at the best U1
  price, and at Kalshi's taker price where Kalshi quotes L; ROI with the decision CI, no word, no family (Devin's own venue allows overs
  only; about 28% of picks on the gate capture)."
- **S5. Fee rounding descriptive.** Add to section 6: "Descriptive: Kalshi-priced picks re-costed with the fee rounded up to the cent per
  order at a $1 and a $10 order (configs/parlay.yaml rounds per order)."
- **S6. Information-fair baseline (BEST_PRACTICES 2026-10-22).** Add to section 1: "Comparators and their information: the null is the
  U1 consensus at E, which holds every pre-tip price and therefore at least the information in the model's inputs; the shuffled-pick
  control (10.2) holds the same rows, venues and prices with the model's side removed."
- **S7. Power text**: add "Guard 10.3(a) needs ROI >= 0 at the second-best price, about 2.1 points below the best on the gate capture
  (label-free), so a PASS effectively needs a best-price ROI of about +0.02 to +0.03 beyond the floor arithmetic."
- **S8. Void (section 7)**: replace "a player who does not play is a VOID" with "a player absent from `player_game_stats` or with
  `minutes` NULL or 0 is a VOID".
- **S9. Section 11** "about 0.5 at 300-400 games": restate from the M4 re-run (independent draws give 0.62-0.76).
- **S10. CI code**: `nba/truth/bootstrap.py` does not exist (`nba/truth/` holds `metrics.py`, `walkforward.py`). Name the scorer
  function that computes the decision CI; the M1 self-test is its coverage test.
- **S11. Descriptive winner's-curse table**: ROI by the best venue's de-vigged P(pick side) minus `p_cons` in bins (< -0.04, -0.04..-0.02,
  -0.02..0, >= 0) and by Kalshi quote age (< 10 min, >= 10 min).

## Biggest risk if a future PASS is wrong
A pass carried by stale or mistaken best prices that were never executable, concentrated on a few players the model persistently
misreads. M2/M4 and guard 10.3 address it; S11 makes it visible.

## Not verified
- The scorer does not exist yet; the rule's own precondition ("the scorer is written and reproduces gate.json") is open.
- Kalshi scalar `settlement_value_dollars` handling for DNP (raw pages not read: they carry settlement and are outcome-linked).
- Whether the-odds-api's US book prices are executable in Devin's state.
- That `forward_predictions.made_at` is never rewritten after the fact (assumed from the writer; not audited here).

## Drafted rows (not appended)
- DECISIONS: `| 2026-11-09 | BEST_PRICE_FORWARD pre-freeze adversary review: NOT_READY (Verdict: BLOCKED); six MUST edits (self-test band unreachable at Read 1, book known-at filter, word order, player clustering, S3 label collision, deterministic order); overs-only descriptive slice recommended; window judged clean. docs/reviews/best_price_forward_prefreeze_2026-11-09.md | Freeze only after the MUSTs, a gate re-run and a re-review. | A re-review finding the revision introduces new contradictions. |`
