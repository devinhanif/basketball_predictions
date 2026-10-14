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

Verdict: BLOCKED
