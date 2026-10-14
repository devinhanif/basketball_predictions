# ALT_PARLAY_X pre-freeze review (adversary), 2026-10-14

Reviewed: `docs/prereg/ALT_PARLAY_X.md` (DRAFT; commit 391ef56, merged at 4628557; above-the-line sha256 at review
time `a8b34007ac68aca84cf8abbf6d539234d6c10134dc3e1b161f7b731698445958`), `research/eval/alt_parlay_x_gate.py` (sha256
303d67ce...), `tests/research/eval/test_alt_parlay_x_gate.py`, `reports/prereg_alt_parlay_x/gate.json` (sha256 d16d70ac..., which
matches the rule).

No outcome exists, and none was computed. No `y` was read. Season 2025 was not loaded (`SEASONS = (2023, 2024)`; `priced_rungs`
asserts `season <= MAX_SEASON`). DuckDB was opened `read_only=True`. Attack script: scratchpad `p1.py` (model-only; not in the
repo). It rebuilt the committed construction and simulated leg outcomes `y ~ Bernoulli(model P)`, independent across legs
(S = 1,000, seed 99).

**Model class:** a stored quantile/distribution layer composed into products of leg probabilities (a set/joint model with
an independence assumption). Nothing is fitted.

**Overall:**
- The construction is clean. It is seeded, involves no hand selection, and is deterministic. `p1.py` reproduced all 20
  season x pool x size parlay counts in `gate.json` exactly (0 mismatches).
- The price convention is consistent with the legs.
- The date clustering is honest about leg reuse.
- Four text defects block the freeze. Two words, CALIBRATED and INDEPENDENCE SUPPORTED, have no minimum-data gate, and the
  simulation shows they are unreachable in most cells (non-negotiable 8).
- The guard against pulled late-news lines that blocked ALT_LINE_70 v2 (B4) was not carried into this rule.

## Blocking (fix before hashing)

**B1. T1 and T2 have no minimum-data gate, and in most cells their "good" words cannot be reached (lines 31-32, 43-61, 66).**
The gate sizes only the ROI. I sampled each estimator under the most favourable truth: legs calibrated and exactly independent,
with no same-night shocks, so these widths are lower bounds. The 95% half-widths:

| Cell | Parlays 2023 / 2024 | T1 gap half-width 2023 / 2024 | T2 rho half-width 2023 / 2024 / pooled | ROI half-width pooled (sim) | Gate cons. / indep. |
|---|---|---|---|---|---|
| ge70 2 | 2,407 / 930 | 0.034 / 0.054 | 0.040 / 0.066 / 0.035 | 0.055 | 0.130 / 0.033 |
| ge70 3 | 2,481 / 879 | 0.040 / 0.064 | 0.031 / 0.051 / 0.026 | 0.091 | 0.187 / 0.045 |
| 55 2 | 3,702 / 3,598 | 0.018 / 0.018 | 0.032 / 0.033 / 0.023 | 0.036 | 0.130 / 0.030 |
| 55 3 | 3,200 / 3,254 | 0.015 / 0.016 | 0.020 / 0.021 / 0.015 | 0.055 | 0.191 / 0.044 |
| 65 2 | 3,488 / 2,790 | 0.022 / 0.028 | 0.033 / 0.038 / 0.026 | 0.043 | 0.123 / 0.029 |
| 65 3 | 3,156 / 2,804 | 0.022 / 0.029 | 0.021 / 0.027 / 0.017 | 0.075 | 0.184 / 0.043 |
| 75 2 | 2,369 / 892 | 0.034 / 0.055 | 0.040 / 0.066 / 0.035 | 0.057 | 0.132 / 0.034 |
| 75 3 | 2,430 / 855 | 0.041 / 0.067 | 0.032 / 0.052 / 0.027 | 0.096 | 0.190 / 0.047 |
| 85 2 | 215 / 125 | 0.101 / 0.133 | 0.135 / 0.168 / 0.103 | 0.157 | 0.260 / 0.096 |
| 85 3 | 139 / 79 | 0.182 / 0.233 | 0.142 / 0.180 / 0.113 | 0.382 | 0.608 / 0.177 |

What the table shows:
- **INDEPENDENCE SUPPORTED** needs the interval inside [-0.02, +0.02]. Even under exact independence it is unreachable in
  every per-season cell (the narrowest is 55-3, at 0.020 / 0.021, against a 0.020 bound). Pooled over seasons, it is reachable
  only in 55-3 (0.015) and 65-3 (0.017), and only with |rho| near 0. **For ge70 2-leg the pooled lower bound is 0.035, so the
  word cannot occur.** Yet the prior (line 66) puts P = 0.60 on it.
- **CALIBRATED** (T1) needs the interval inside +-0.03. It is unreachable per season in every ge70, 75 and 85 cell
  (0.034-0.233). It is reachable only in pools 55 and 65. The prior puts P = 0.15 on it for ge70 2-leg.
- The rule also never says whether T2 is decided per (pool, size, season) or pooled.

*Fix (text before the hash, plus a gate rerun):*
1. Add the model-only T1 and T2 widths to the gate (the `p1.py` method, or a conservative date-clustered analogue).
2. Declare the T2 cell. My recommendation: per (pool, size), pooled over seasons, with a per-season sign check.
3. State in the rule which cells can issue CALIBRATED and INDEPENDENCE SUPPORTED. Every other cell reports gap and rho with
   their CI and can say only OVERCONFIDENT / UNDERCONFIDENT / DEPENDENT / INCONCLUSIVE.
4. If Devin wants an independence word for ge70, change the scale before the freeze. A defensible choice is excess joint hit
   rate in probability units: |mean(hit_joint - prod P), centred| <= 0.01. That is new rule text for Devin to sign; it is not
   to be tuned after the result.
5. The modeler may restate the ledger priors (draft rows, no outcome exists) but must say that they were restated and why.

**B2. Three unstated details that change words (lines 31-32).**
1. The BH family for T1's directional words is not given. My reading is 20 cells (5 pools x 2 sizes x 2 seasons) or 16 if 85
   is dropped. Fix: state m.
2. The BH family for T2's DEPENDENT word is not given (no BH is mentioned for it at all). Fix: state m.
3. T2 sums over "all pairs of legs that appear together", but the rule does not say whether a pair counts once or once per
   parlay it appears in. For 3-leg ge70 in 2023 these differ: 7,443 pair appearances against 5,198 distinct pairs. Fix: state
   "with multiplicity" (what makes rho the parlay-relevant average) or "distinct".

**B3. The pulled-line guard from ALT_LINE_70 B4 is not inherited (lines 16, 40).**
- The legs exist only if BetRivers still posted the line about 9 minutes before tip. 3.0-4.1% of played t60 units are pulled by
  t5 (ALT_LINE_70 step 3; my `a4.py`, re-review 2026-10-14). A line pulled for late minutes news is a likely losing over, and
  dropping it flatters every parlay that would have held it. A parlay loses on any one bad leg, so the bias compounds with m.
- The attack list (line 40) has no counterpart of ALT_LINE_70's attack (viii). Its t60 rerun is "descriptive" and has no sign
  requirement.

*Fix:*
1. Line 16: add the ALT_LINE_70 sentence "Which bets exist cannot be guaranteed the same way: it depends on lines still posted
   at T-9".
2. Line 40: add the attack "units quoted at t60 and absent at t5 (played) enter the pools at their t60 price, the construction
   is re-run with the same seeds, and the ROI sign must hold".
3. Also add ALT_LINE_70 attack (vii), the report-stamp drop on the t60 rerun.

**B4. The construction is fixed only in prose, and the hash does not cover the code (lines 4, 98).**
- The `prereg(ALT_PARLAY_X)` sha256 covers the markdown. The construction lives in `alt_parlay_x_gate.py` and imports `EPS`,
  `SECONDARY_BINS`, `THRESHOLD` and `priced_rungs` from `alt_line_70_gate.py`. An edit to either file changes the parlays
  without changing the hash.
- `Generator.choice` / `integers` streams are not guaranteed identical across numpy versions (2.5.3 here).

*Fix (rule text):*
1. Record the sha256 of both files: `303d67ce8ed22532f145512f2d17691c3ba8f3f0f3d22b6fd2eb91d795c8d44b` and
   `8da31748003500f52f219ccc8c7bea71ee5ee91d56e30bc966112433e6413277`, or the values at freeze if they change. Record the numpy
   version too.
2. Require the harness to assert both hashes.
3. Require the harness to assert that the reconstructed per-cell parlay counts equal `gate.json`, and to write a sha256 of the
   leg-index table as a fingerprint.

## Answers to the brief

1. **Is the seeded construction free of hand selection, and fixed before results?** Yes.
   - One leg per unit is chosen by a seeded uniform draw after a total sort. Keys are unique after de-duplication, so the
     order is deterministic.
   - Games are drawn first, then one leg per game. Duplicate sets are dropped with no refill. Seeds are `[2026, season,
     pool_index(, m)]`.
   - The tests check one leg per unit, determinism, distinct games, one date and product odds.
   - It is fixed in code before any outcome, but not yet under the hash (B4).
2. **Twenty draws per date reuse legs. Is the CI honest?** Yes, given date clustering.
   - A leg belongs to one game and one date, so all reuse happens within a date cluster.
   - The construction's own randomness (which pairs were drawn) sits inside each date's mean, so the date bootstrap includes it.
   - What date clustering does not cover: a player's model bias that persists across dates. *Recommend (non-blocking):* a
     week-clustered interval as a descriptive sensitivity.
3. **Is "20" a forking choice?** Not a forking path. It was fixed after counts and before outcomes, and the rule makes a change a
   new id. It does define the estimand: each date weighs min(20, the number of distinct sets that date allows).
   - Line 23's "keep every date at comparable weight" is not true of thin dates. In ge70 2024, 92 of 141 dates have two or more
     games with legs, and many dates allow only 1-3 distinct pairs. *Fix (minor):* say "busy dates are capped at 20; thin dates
     weigh less".
4. **Independence test design.** Centring by pool-season removes common bias. That is right, because a shared overconfidence is
   T1's job. Residual date-level drift (month-block refits) will still show up as rho > 0. That is real dependence for a parlay
   holder, so it is acceptable, but the rule should say so. The power problem is B1; the undefined cell and multiplicity are B2.
5. **Price convention.** D = product of the leg over decimals at t5, from the same capture and rows as the legs. That is
   consistent, and it matches ALT_LINE_70 arm A. The overround compounds (about 1.19 / 1.30) and the rule reports it.
   - *Major, non-blocking:* there is no single placement time. Each leg is priced at its own game's T-9, and each forecast is
     made at its own game's T-60. A real ticket placed before the earliest tip would carry staler prices and forecasts for the
     later games. This does not flatter the model against the price, because each leg is aligned. But the plan is not a
     placeable ticket. *Fix:* add this to "What it cannot say" (line 41).
6. **"PROFITABLE is near-unreachable" and "calibration is the informative part".** The rule says "nearly out of reach by design"
   (line 46), but only on the conservative bound. Under calibrated independent legs, the simulated pooled ROI half-width is
   0.036-0.096 in the gated cells (0.055 for ge70 2-leg). The model claims +17.3% EV for ge70 2-leg in 2024. So PROFITABLE is
   reachable if the model is right, and out of reach only if its true edge is below the compounded margin, which is the prior.
   As written, the informative part is T1's **directional** word: a gap of -0.08 is detectable against half-widths of
   0.034-0.054. CALIBRATED and INDEPENDENCE SUPPORTED mostly cannot be issued (B1).
   *Fix:* in the Question section and in "What Devin must sign", say plainly: "PROFITABLE needs a realised ROI of roughly +0.05
   to +0.13 in the best cell. The likely informative outputs are the T1 gap (OVERCONFIDENT or not) and the compounded-margin
   break-even table."
7. **Decision words.** PROFITABLE / NOT PROFITABLE / INCONCLUSIVE are per (pool, size), pooled, with a per-season sign check
   and BH over 10. NOT PROFITABLE (hi < +0.02) is reachable at the zero-skill compounded margin of about -0.17 / -0.26. They
   are well formed. *Minor:* state that the two 85 cells' p values count in m = 10 although they get no word.
8. **Gate per pool.** 8 of 10 cells pass, and both 85 cells fail (340 and 218 parlays; 0.260 and 0.608), reproduced. Per season,
   2024 fails in most cells. The decision is pooled, which is consistent with ALT_LINE_70.
9. **2025 excluded.** Verified in code: the season tuple, the assert in `priced_rungs`, and the files read are
   `model_rows_{2023,2024}` and `pairs_{2023,2024}`. The odds query is filtered by season. *Minor:* line 6 says "asserted
   before any read", but `run_gate` reads the pairs files before the first assert (inside `priced_rungs`). The harness should
   assert `max(SEASONS) <= 2024` first, as `alt_line_70_gate.run_gate` does at line 379.

## Six checks (pre-result; applicable ones)

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels | n/a before a result; shuffled-P (50 seeds, through the construction) is in the attack list | - | - | n/a |
| 2 | Planted future / as-of | `priced_rungs` timing assert (0 violations, asserted in the gate); pulled t60 lines | 2023-24 BetRivers t5 rungs | timing pass; pulled-line selection is unguarded | **fail (B3)** |
| 3 | Missingness | DNP legs excluded before construction (played-only model rows) | all legs | a post-tip filter, but symmetric with a book's void; stated as an approximation (line 25) | pass (minor) |
| 4 | Knockout | n/a (no feature model) | - | - | n/a |
| 5 | Replication | per-season sign required; t60 rerun; one-pool-season check | - | in the rule | pass |
| 6 | Integer support | legs use ALT_LINE_70 `p_over_at` (k = point + 0.5) | all legs | same convention | pass |
| - | Construction reproducibility | `.venv/bin/python -W ignore <scratchpad>/p1.py` | 20 season-pool-size cells | 0 count mismatches vs `gate.json` | pass |
| - | Word reachability (power) | `p1.py`, S = 1,000, seed 99 | table in B1 | INDEPENDENCE SUPPORTED reachable in 2 of 10 pooled cells; CALIBRATED unreachable in ge70, 75 and 85 | **fail (B1)** |

## Verification (unpiped)
- `.venv/bin/python -m pytest -q tests/research/eval/test_alt_parlay_x_gate.py tests/research/eval/test_alt_line_70_gate.py
  --no-cov -p no:cacheprovider`: 15 passed, exit 0.
- `ruff check` exit 0, `ruff format --check` exit 0, `mypy` exit 0 (on both gate files and the test).
- `p1.py` exit 0.

## Playbook check
1. **Information or capacity?** Neither. A transformation of an existing forecast.
2. **Enough data for this class?** For ROI, pooled, yes in 8 of 10 cells. For the joint/independence words, no in most cells
   (B1). Joint models need many more pairs than marginal ones, and the gate did not size them.
3. **Test validity?** Real-tip timing holds and the legs are aligned. Pulled-line selection is unguarded (B3). There is no single
   placement time (answer 5).
4. **Lineage and inherited guards:** ALT_LINE_70's timing, de-duplication, integer support and date clustering are inherited.
   ALT_LINE_70's attacks (vii) and (viii) are **not** (B3).
5. **What it scores against:** joint reliability, a dependence coefficient, and ROI at a multiplied price.

Class check (set and joint models): the power claim at line 80 covers ROI only. It needs the T1 and T2 rows (B1).

## Corrected family and power note
- Money words: BH m = 10. Pooled sim half-width 0.036-0.096 in the gated cells, conservative 0.12-0.19.
- T1 directional words: m is unstated (B2). Detectable gap about 0.02-0.07, depending on the cell.
- T2: the family is unstated. The detectable |rho| is about 0.015-0.035 pooled, under independence.
- "Underpowered, cannot conclude" is the expected T2 outcome outside pools 55 and 65. It is not evidence of independence.

## Single biggest risk
If a PROFITABLE word ever appears, the most likely reason it is false is pulled late-news overs. BetRivers drops a line between
T-60 and T-9 for a player whose minutes were cut, and every parlay that would have carried that losing leg disappears (B3).

## Not verified
- The real bootstrap widths with same-night shocks (no outcomes; the sim is a lower bound).
- How BetRivers actually prices cross-game parlays (product of decimals assumed, per Devin).
- Whether `game_date` is the local slate date for late West-coast tips.
- Whether the future harness will import the construction unchanged (B4 asks it to assert this).

## Recommended changes (not applied)
- B1-B4 as above.
- The minor items in answers 3, 5, 6, 7 and 9.
- A week-clustered sensitivity.

The ledger rows for the priors are already drafted in `docs/PREDICTIONS_LEDGER.md`. The B1 restatement, if made, needs a dated
note there. The DECISIONS row for Devin's ask is drafted in `docs/reviews/ALT_LINE_70_prefreeze_2026-10-14.md` (v3 review).

(First-review verdict: BLOCKED; superseded by the re-review below.)

## Re-review, 2026-10-14 (revision 2: branch commit 80b7cf3, merged at ae4e9dd)

What I checked: `git diff 4628557 HEAD -- docs/prereg/ALT_PARLAY_X.md research/eval/alt_parlay_x_gate.py` and the whole
revised rule, read again for new contradictions. The above-the-line sha256 at re-review time is
`0071937a0d2ea88075913d865ddadab042a39aff53a5ab2ca12f8d37b169b6d2`.

Conditions: no `y` was read, season 2025 was not loaded, and DuckDB was opened read-only.

**The gate reproduces byte for byte.** I re-ran it to the scratchpad:
`.venv/bin/python -W ignore -m research.eval.alt_parlay_x_gate --data-root . --out <scratchpad>/gate_repro.json`. It exits 0
with sha256 `31e93881...`, and `cmp` against the committed `gate.json` exits 0. The rule's widths table matches the
`pooled.*.null_halfwidths` in `gate.json`. My first-review simulation agrees independently: ge70 2-leg T1 per season
0.034 / 0.054, which pools to about 0.029, against the gate's 0.0281.

### The blocking items
| Item | Status | Where / evidence |
|---|---|---|
| B1 (T1/T2 had no gate; positive words unreachable) | **FIXED** | Line 34: words are decided per (pool, size), pooled, with a per-season sign check. Lines 35-36: CALIBRATED and INDEPENDENCE SUPPORTED are restricted to the cells that can issue them. Lines 48 and 65-80: model-only widths are in the gate, along with the can-issue table and the list of cells that cannot issue positive words (T1: ge70 2/3, 75 2/3; T2: ge70 3, 75 3; both 85 cells get no word). Line 80 says INCONCLUSIVE there means underpowered. Line 36 and line 124: the new E scale, its 0.01 point limit and the 0.02 can-issue bound are disclosed as set after the widths and before any outcome. Line 89 and `PREDICTIONS_LEDGER.md` (last row): the priors are restated, and the earlier row stands. |
| B2 (unstated families and multiplicity) | **FIXED** | Line 34: three BH families of m = 10 (T1 directional, T2 DEPENDENT, money), with the 85 cells' p values counted in m. Line 36: pairs are counted with multiplicity; the code matches (`estimators`, loop over all pairs of columns of each parlay row). |
| B3 (pulled-line guard) | **FIXED** | Line 19 adds the statement and says the bias compounds with m. Line 82 gives the counts per pool and season (1.5%-5.3%). Line 44 adds attacks (vii) and (viii) with a sign requirement, implemented by `pulled_units` and `pool_legs_with_pulled`. |
| B4 (construction identity) | **FIXED** | Lines 131-132: both code sha256 values match the files (`shasum`: 169b8194..., 8da31748...), the numpy version is 2.5.3, the pooled counts are listed, the per-cell `fingerprint_sha256` is stored in `gate.json`, and the harness asserts all of them. `run_gate` now asserts `max(SEASONS) <= MAX_SEASON` before any read, which makes line 7 true. |
| Minor items from the first review | done | Lines 16 and 143 say plainly that PROFITABLE needs roughly +0.05 to +0.13. Line 45 adds that there is no single placement time. Line 26 covers thin-date weights. Line 36 adds a week-clustered interval of E. |

### Is every word reachable on the real gate widths? (non-negotiable 8)
The bar for each word, on the model-only widths (a lower bound, so the real bar is higher):
- **CALIBRATED** (|gap| <= 0.02 and interval inside +-0.03): issued only where the pooled width is <= 0.02 (0.0107-0.0184),
  which leaves |gap| up to about 0.012-0.019. Reachable in all 4 listed cells.
- **INDEPENDENCE SUPPORTED** (|E| <= 0.01 and interval inside +-0.03): issued only where the width is <= 0.02 (0.0101-0.0191).
  Reachable in all 6 listed cells, but only barely in ge70 2-leg (0.0191) and 75 2-leg (0.0189). Both sit just under a
  bound that was chosen after the widths were seen. This is disclosed and involves no outcome, so it is acceptable. With
  same-night shocks the real width will be larger, so in those two cells INCONCLUSIVE is the likely word, and the restated
  prior (0.30) says so.
- **OVERCONFIDENT / UNDERCONFIDENT / DEPENDENT** need the statistic beyond 0.02 (T1) or 0.01 (E) and beyond the width.
  Reachable in every gated cell for effects of about 0.011-0.034 (T1) and 0.010-0.025 (E).
- **PROFITABLE / NOT PROFITABLE** were checked in the first review; both are reachable.

The cells that cannot issue positive words are listed (lines 69-80). PASS.

### New problems introduced by the revision
**B5 (blocking, one line): the p-value behind the T1 and T2 BH q is no longer defined (lines 35-36).** The first draft said
"the p-value is the date-clustered bootstrap p, the only cluster available here". Revision 2 deleted that sentence (visible in the
diff) and put nothing in its place. Only the money word still says where its p comes from (line 38, one-sided). OVERCONFIDENT,
UNDERCONFIDENT and DEPENDENT all need "BH q < 0.05". Without a stated p, the harness author has to choose one-sided or
two-sided, the bootstrap type and the +1 correction, and that choice changes q and so the word.
*Fix:* at the end of line 34, add: "For T1 and T2, p is the two-sided date-clustered bootstrap p of the cell's pooled statistic
against 0 (B = 5,000, `default_rng(2026)`, with the +1 correction), the same resample of dates as the interval."

Non-blocking (fix in the same pass):
- **N1 (major, disclosure): the +-0.03 margin of probability means a different amount of money in each cell.** The median
  parlay decimal D is about 1.8-2.0 for ge70/75 2-leg, 2.5-3.0 for 55 2-leg, and 4.0-5.2 for 55 3-leg (`gate.json`
  `decimal_p10_med_p90`). So an interval edge of 0.03 is worth an ROI of about +-0.06 (ge70 2-leg) up to about +-0.14 (55 3-leg).
  The 0.01 point limit on E is worth +-0.02 to +-0.05. The mean joint P in 55 3-leg is 0.16, so +-0.03 there is about +-19%
  relative. A CALIBRATED or INDEPENDENCE SUPPORTED word in pools 55 and 65 is therefore a weak equivalence when measured in
  money. *Fix:* one sentence after line 80 giving D x 0.03 per cell.
- **N2: attack (viii) re-draws everything, not only the added legs.** `pool_legs_with_pulled` concatenates the pulled rows and
  then calls `pool_legs`, which assigns `u` over the merged sorted table. Every unit's leg can change, and `draw_parlays` re-draws
  every date. So the attack's ROI change mixes the pulled-line effect with construction noise. That makes the sign test
  stricter, not more lenient, so it cannot hide the leak. It does stop line 44's "same seeds" from meaning "same parlays plus
  the pulled legs". *Fix:* either say this on line 44, or report the attack as the ROI difference over the parlays that contain
  at least one pulled leg.
- **N3: two lines still conflict about PROFITABLE.** Line 50 still says "PROFITABLE is nearly out of reach by design" (conservative
  bound only), while line 16 gives the realistic +0.05 to +0.13. *Fix:* on line 50, append "(on the conservative bound; line 16
  gives the realistic range)".
- **N4: coupling.** ALT_PARLAY_X pins `alt_line_70_gate.py`, so any edit to that file stops this rule (the harness assert)
  unless the rule records a new hash. ALT_LINE_70's own harness must import that file, not edit it. Note this for the main session.

### Verification (unpiped)
- `.venv/bin/python -m pytest -q tests/research/eval/test_alt_parlay_x_gate.py tests/research/eval/test_alt_line_70_gate.py --no-cov -p no:cacheprovider`: 18 passed, exit 0.
- `ruff check` exit 0, `ruff format --check` exit 0, `mypy research/eval/alt_parlay_x_gate.py` exit 0.
- Gate rerun exit 0; `cmp` exit 0.

### Playbook check (re-review)
The class is unchanged. Enough data: the joint words are now sized, and the positive words are limited to the cells that can
carry them. Test validity: the pulled-line guard is inherited. What remains is the lower-bound nature of the widths (no
same-night shocks) and N1. Lineage: ALT_LINE_70's attacks (vii) and (viii) are now carried.

**Single biggest risk (re-review):** unchanged. Pulled late-news overs flatter the parlays if a PROFITABLE word appears.
Attack (viii) now guards it.

**Not verified:** real bootstrap widths; BetRivers' real parlay pricing; whether the future harness writes p as B5 asks.

Verdict: BLOCKED
