# Adversary post-result read: WORKLOAD_PTS_V2 (T521) and LINEUPS_KNOWN_REBASE (T522)

Written 2026-10-27 23:23 CDT (from `date`). Read-only on main at 43656d9. Season 2025 was never loaded. No harness was re-run.
Everything below is re-derived from the stored checkpoints (`data/ops/workload_pts_v2_cache/`, `data/ops/lineups_known_rebase_cache/`,
`data/ops/recent_role_full_cache/`, `data/ops/workload_cache/pts__B.npz`, `reports/lower_tail/components_*.npz`) and the two
`results.json` files. The scratch scripts are in the session scratchpad and are named under "Commands"; none is under `nba/`.

| item | rule | verdict | the numbers that matter (n per season ~27.6k props rows / 1,318-1,320 games; 33.1k P(play) rows; game-clustered 95%) |
|---|---|---|---|
| 1 WORKLOAD_PTS_V2: null on the 22-column increment | 20d480f7 (b9669de) | **STANDS** | WF - FULL 2023 -0.0002 [-0.0031, +0.0026]; 2024 -0.0020 [-0.0043, +0.0003]. Positive control FULL - A0 -0.00835 / -0.00831 equals T467 to 1e-15. |
| 1b T452 re-judged (descriptive deliverable) | same | **STANDS: T452's gain is real and is recent-role minutes** | B - PL27 -0.0107 [-0.0147, -0.0067] / -0.0100 [-0.0139, -0.0065]; honest size B - A0 -0.0071 / -0.0095; recent-role share 87% / 80% (HEADS - A0 over B - A0, same pi). |
| 2 LINEUPS_KNOWN_REBASE | 876e5804 (8fbf72c; frozen a48ef7b3 at 31c94e1) | **WOUNDED** (the numbers stand; the T522 reading overstates P(play) and understates the tripwire) | N1 pts 2024 headline -0.0425 [-0.0498, -0.0358], P2S -0.0203 [-0.0259, -0.0145]; N4 bench headline -0.0220 against pess2 -0.0021 / P2S -0.0003; RT_P2S - R on all rows **+0.0357** [+0.0295, +0.0421] (harm). |

Verdict: APPROVED

(Approved means both results may stand as recorded, provided the drafted annotation rows below are appended. Nothing here is a
promotion, and neither result touches the live path.)

---

## Item 1: WORKLOAD_PTS_V2

### What was confirmed
* **Positive control.** `FULL - A0` = -0.008350104998763881 (2023) and -0.008314476376969474 (2024). The harness reads T467 from
  `reports/prereg_recent_role_full/results.json` (`real_ctx`, workload_pts_v2_eval.py lines 365-370), which is a 2026-10-12
  artifact, so the comparison is not self-referential. The values match to 1e-15, against a 1e-4 tolerance. The reproduction gates
  are bit-exact: P0C equals the stored components (max diff 0.0), the FULL refit equals `heads_pts_R.npz` (0.0), and the FULL pi equals
  `pi_R.npz` (0.0). The frame pin 47b569fb was reproduced (55,320 / 55,320 rows).
* **Planted-signal control.** `test_shuffle_shares_one_permutation_across_arms_and_the_guard_math` passed (41 tests passed across both
  harness suites; see Verification). Gate plants: same-game, 308 rows of the edited day identical, 13,770 later rows change. Future:
  14,466 rows identical. Schedule: 6,618 rows identical. Heads walk for WF: 10,064 rows before the 2025-01-24 cutoff with max diff 0.0,
  and the control's 12,790 later rows change (max 97.65). The shuffle guard itself was not run. That is correct under rule 4: it is
  conditional on rules 1-2 passing, and a shuffle cannot rescue a null.
* **Arm saw it.** The 22 columns hold 12.6% of WF's summed gain importance, so the arm read them.
* **Placebo rule.** `research/recent_role/placebo.py` `past_only_source` draws one source row uniformly from the same player-season
  among rows dated strictly before the target's date, and shares it across all columns. It returns NaN iff no such row exists (gate
  W6: 893 NaN rows = 893 `n_season == 0` rows). The source row's values are its own as-of features, so the placebo carries no future
  information. This replaces T452's defect D2 correctly.
* **Naming knockouts.** Gain left when each family is dropped, as a share of the 2024 WF - FULL of -0.00201: LOADX -0.00055 (28%),
  SCHEDULE -0.00044 (22%), TRAVEL -0.00163 (81%). None is at or below 20%, so no family is named. SCHEDULE sits close to the line, but
  that is moot for a null.
* **Re-derived from the caches** (scratch `wpv2_rederive.py`): every rule and descriptive contrast in results.json reproduced to the
  printed digits.

### The deliverable: T452 re-judged (descriptive; per-number 95%; not multiplicity-adjusted)
| contrast (pts integer CRPS, game-clustered) | 2023 (n 27,619 / 1,318 g) | 2024 (n 27,701 / 1,320 g) |
|---|---|---|
| B - PL27 (T452's failed condition, fair placebo) | **-0.0107 [-0.0147, -0.0067]** | **-0.0100 [-0.0139, -0.0065]** |
| B - A0 (refit) | -0.0071 [-0.0110, -0.0035] | -0.0095 [-0.0132, -0.0060] |
| B - A0 (cached T452 B) | -0.0071 (identical) | -0.0082 [-0.0119, -0.0048] |
| PL27 - A0 (is the placebo neutral?) | **+0.0036 [+0.0005, +0.0068]** | +0.0005 [-0.0017, +0.0028] |
| HEADS - A0 (the 5 recent-role columns, same A0 pi as B; T467's heads) | -0.0062 [-0.0095, -0.0029] | -0.0076 [-0.0110, -0.0043] |
| B - HEADS (the 22 others on the same pi) | -0.0009 [-0.0038, +0.0020] | -0.0019 [-0.0042, +0.0005] |
| recent-role share of B - A0 (1 - (B-HEADS)/(B-A0)), paired game bootstrap | 0.87 [0.52, 1.41] | 0.80 [0.57, 1.07] |
| harness overlap 1 - (WF-FULL)/(B-A0) (FULL pi on WF/FULL) | 0.97 [0.56, 1.48] | 0.79 [0.53, 1.04] |
| B - PL27 after dropping B's best month block | -0.0072 [-0.0118, -0.0028] | -0.0089 [-0.0128, -0.0050] |

Plain reading. **T452's gain survives a fair placebo**: it now meets the -0.003 condition in both seasons. **Most of it,
80-97% by either decomposition, is the five recent-role columns, that is, FULL.** The other 22 columns add nothing distinguishable
from zero. Two qualifications:
1. **B - PL27 overstates the size.** In 2023 the past-only placebo is worse than A0 (+0.0036, CI excludes 0). That is a capacity and
   noise cost of 27 stale columns, not a leak. So B - PL27 (-0.0107) is about 1.5 times B - A0 (-0.0071). The honest size of T452's
   gain is B - A0: -0.0071 / -0.0095 (refit), or -0.0082 on T452's own cached 2024 arrays.
2. **The 2024 B refit does not equal T452's cached B** (`match_1e-6: false`). Re-derived: 2023 is identical row for row, but 14,905
   rows in 2024 blocks 10-17 differ (max quantile diff 5, cached mean CRPS worse by +0.00127). P0C and FULL are bit-identical, so the
   change sits in some of the 22 non-recent workload columns for 2024-25 rows, sometime between 2026-10-11 23:38 and the 2026-10-27
   frame. It moves B - HEADS 2024 from -0.0006 (cached) to -0.0019 (refit), so it probably accounts for part of WF - FULL 2024's
   -0.0020 (p 0.091). It does not move the verdict: the null holds, and the T452 reading holds on both versions (cached B - PL27 2024
   is -0.0087). It is still an unexplained data change under a rule pinned only from 10-27 onward.

### Power and family
Family m = 1 (pts, declared; BH is the identity). The family actually run is the same one. The 2024 MDE printed by the harness
(0.0023) is the CI half-width, i.e. about 50% power. At 80% power it is about 2.8 x SE ≈ 0.0033. The null excludes the -0.005 floor in
both seasons (CI lower bounds -0.0031 / -0.0043). "No effect larger than 0.0043 in 2024" is the right sentence.

### Six checks (item 1)
| # | check | command / source | n | result | pass |
|---|---|---|---|---|---|
| 1 | shuffled labels | rule 4, conditional on rules 1-2; unit test of the shared permutation | 50 seeds x 2 seasons (not run) | not run, primary failed (correct per rule); planted-signal unit test passed | n/a (null) |
| 2 | planted future / lag | gate.json plants; heads-walk plant at 2025-01-24 | 14,466 / 6,618 / 10,064 rows | identical before, controls change; extra-day lag **not run** | pass (lag unverified) |
| 3 | missingness | gate.json, the 22 by realised-minutes bucket | 55,320 | max gap 0.19 pp (wl_three_in_four) vs limit 2.00; 609 NULLs = team's first game | pass |
| 4 | knockout | results.json naming | 27,701 (2024) | no family at or below 20% left | pass |
| 5 | replication | wpv2_rederive.py, drop best block, both seasons | 23,097-27,505 | every sign holds (table above) | pass |
| 6 | integer support | `PR.crps_int` on `to_integer_support` for every arm | all | every arm on the integer grid | pass |

---

## Item 2: LINEUPS_KNOWN_REBASE

### Reproduction and pins (check 1: raw vs canon)
* P0C and FULLC equal the cache for all 4 stats (max diff 0.0, inputs identical); FULL pi 0.0; R refit 0.0. No drift.
* Canonical pins match the amended rule: props 231e8648 and P(play) 372dacf3. The raw unrounded hash a3274c6d differs from the
  original pin 9359bcf4. Amendment 1 predicts exactly this: `t30_vac_starter_min` jitters at 2.8e-14 from a polars `group_by` sum.
  `test_amendment1_pins_and_canonical_hash_ignore_float_jitter_only` passed. Rounding to 1e-9 can hide only sub-1e-9 changes in a
  minutes sum. That is acceptable.
* **Rule integrity.** The rule hash changed after the freeze under the same id: a48ef7b3 at 31c94e1 (19:09:02 -0500), then 876e5804 at
  8fbf72c (19:57:57 -0500). The diff is the Amendment 1 block only (verified with `git diff 31c94e1 8fbf72c`). It touches the identity
  pins and the plant comparison, not arms, metrics or the reading. It precedes the first fitted artifact (cache mtime 22:20:00), and
  DECISIONS logged it at 19:50. I accept it as a pre-fit identity repair. CLAUDE.md's "a changed rule is a new id" was nonetheless not
  followed, and T522 cites only the amended hash. Amendment 1 also types a time ("measured 2026-10-27 19:4x CDT"), which is a defect
  under BEST_PRACTICES "Timestamps come from the clock". I could not verify that no arm was fitted under a48ef7b3 before 19:57; cache
  files are overwritten on rerun.

### Plants and shuffle (checks 3 and 2)
* Plant at 2025-01-25: the `t30_*` of 86,580 earlier rows are identical. The only difference is the vac jitter (2.8e-14), which equals
  the unedited-rebuild jitter. Control: flipping the cutoff day's flags changes 363 of 363 rows. Pass. The plant is feature-level only;
  unlike WPV2 there is no heads-walk plant. The rule did not require one; noted, not verified.
* Starter-flag shuffle (single seed), T30SH - P0: pts +0.0004 [-0.0020, +0.0029] / +0.0004 [-0.0017, +0.0023]; reb +0.0008 / +0.0003;
  ast -0.0005 / +0.0003; fg3m -0.0004 / +0.0001; every CI spans 0. RT_SH - R: +0.0002 [-0.0004, +0.0008] / +0.0002 [-0.0003, +0.0006].
  The flag is not raised. This tests the harness, not the proxy (as the rule says), and the harness manufactures nothing.

### Bridge (check 4)
Continuous T30 - P0, pts 2024: -0.0424 [-0.0497, -0.0358], n 27,701, against T085's -0.0412 [-0.0475, -0.0346], n 27,583. The gap is
0.0012, under 0.008. The integer-support headline (-0.0425) is essentially the continuous value, so support does not matter for pts
here. Pass.

### Is P2S built as the rule says? Yes (code and arithmetic)
* Code. `_served_model` (lineups_known_rebase.py lines 610-619) overrides only `predict_components`. `ContextResidualModel.fit`
  (context_residual.py lines 821-835) builds the heads and the conformal `z_sorted` through `_matrix` and `mean_head.predict` on the
  training and calibration rows, so they read headline columns. `collect_heads` builds `cal_*` the same way
  (recent_role_full_eval.py lines 252-262). Only the test block is served pess2. The P(play) path (`pplay_walk_served`) fits on
  `train` and serves only `tb`. Unit tests: served walk equals plain walk when the served columns are identical, and differs otherwise.
* Arithmetic. Outside late-resolved team-games, P2S test flags equal headline flags, so P2S must equal headline there. Implied from
  results.json (overall = w x late + (1 - w) x outside; w = 0.167 / 0.178):

| pts N1 | late-resolved team-games (n 4,625 / 4,940) | outside (n 22,994 / 22,761) |
|---|---|---|
| headline 2023 / 2024 | -0.0855 / **-0.1000** | -0.0284 / -0.0300 |
| pess2 | +0.0052 / -0.0071 | -0.0237 / -0.0238 |
| P2S | **+0.0319 [+0.0152, +0.0488] / +0.0232 [+0.0068, +0.0392]** | -0.0287 / -0.0298 (= headline, as required) |

So the entire headline-to-P2S gap comes from the 17-18% of rows in late-resolved team-games. In those games, a model trained on box
flags and served pess2 flags is **worse than production**, and the CI excludes 0. reb and ast show the same pattern (P2S late: reb
+0.0123, ast +0.0038 in 2024).

### Can excl-late conditioning manufacture the gap?
Yes, which is why it must not be read as a number. Excl-late drops team-games selected on a post-tip outcome (a usual starter did
not play). That outcome is correlated with how much tonight's lineup differs from the usual one, which is where honest T-30
information is also most valuable. So excl-late is neither a lower bound nor an honest live estimate. The headline minus excl-late
gap (0.0125 pts) is not "the proxy bias": it mixes proxy-only information with real high-value games. The arithmetic above also shows
excl-late adds nothing independent, since it equals P2S outside late games by construction. The rule gives it no weight in the
reading, and the harness reading does not use it. Pass, with one wording point: T522 and the result note should not quote excl-late
next to the range.

### N4: is the P(play) gain mostly the box-score proxy? Yes.
Re-derived from `pplay_arms.npz` / `pplay_R.npz` (scratch `n4_split.py`; log loss `WE._ll`; game-clustered):

| RT - R, all rows | 2023 (n 33,093; DNP 5,481) | 2024 (n 33,127; DNP 5,438) |
|---|---|---|
| headline | -0.0196 [-0.0221, -0.0172] | -0.0237 [-0.0266, -0.0209] |
| share of that gain on DNP rows (16.5% of rows) | **94%** | **72%** |
| share on "surprise DNPs" (R gave p >= 0.9; 310 / 312 rows, ~1%) | 44% | 33% |
| headline on played rows only | -0.0015 [-0.0029, -0.0001] | -0.0081 [-0.0098, -0.0065] |
| bench tonight: headline / pess2 / P2S | -0.0190 / -0.0015 / +0.0013 | -0.0220 / -0.0021 / -0.0003 |
| **P2S, all rows** | **+0.0258 [+0.0209, +0.0306]** | **+0.0357 [+0.0295, +0.0421]** |
| P2S, played rows only | +0.0050 [+0.0036, +0.0064] | -0.0009 [-0.0023, +0.0006] |

Plainly: **the offline P(play) gain is mostly the box-score proxy.** A box-score starter always played, and a usual starter missing
from the box five who then did not play is, by construction, what the box score knows after tip. About 90% of the bench-tonight gain
disappears under the worst case (-0.0220 to -0.0021). Under the live construction (P2S) the T-30 P(play) model is worse than R by
+0.026 / +0.036 on all rows, because it learned "usual starter not in the five => DNP" and is then served fives that do not resolve the
late scratches. The honest gain lies between pess2 and headline, and where it lies depends only on how many late scratches the T-30
announcement captures. No offline data can show that; the live collector's open-decision share (FORWARD section 2.1 item 2) can.
The reading-rule result, "no T-30 P(play) work", is correct.

### N2 / N3 (beyond recent role)
pts N2 pess2 -0.0172 [-0.0216, -0.0131] / -0.0217 [-0.0260, -0.0177], P2S -0.0164 / -0.0218. reb pess2 -0.0078 / -0.0066, P2S -0.0095 /
-0.0072. All CIs are below 0 in both seasons. N3 overlap is 0.09 / -0.04 (pts headline): FULL captures almost none of the T-30 gain.
The reading "pts and reb add beyond recent role; ast conditional; fg3m below floor" stands.

### The FORWARD tripwire
Frozen direction check (FORWARD_PREREG_2026_27.md lines 107-109): a live estimate below pts -0.041, reb -0.018 or ast -0.0089 "is
treated as a suspected leak/pipeline fault and triggers an audit before any use". Those thresholds are the proxy-tip continuous
backtest point estimates. On the live support with real tips, each threshold sits **inside** the honest headline CI:
pts -0.041 in [-0.0498, -0.0358], ast -0.0089 in [-0.0115, -0.0076], **and reb -0.018 in [-0.0208, -0.0147]**. The harness flags only
point estimates below the threshold, so it printed `reb: false`. That understates reb.

Single-look probability that an honest live estimate fires, using a normal approximation and the date-clustered 2024 SE scaled to
each look by the harness's L60 factor (2.04; L30 x sqrt 2, L120 / sqrt 2):

| stat | true live effect = headline | = midpoint of [low, headline] | = low end (P2S / pess2) |
|---|---|---|---|
| pts (L30 / L60 / L120) | 0.56 / 0.58 / 0.61 | 0.18 / 0.10 / 0.03 | 0.02 / 0.002 / 0.000 |
| ast | 0.58 / 0.61 / 0.66 | 0.23 / 0.15 / 0.07 | 0.05 / 0.01 / 0.000 |
| reb | 0.47 / 0.46 / 0.45 | 0.11 / 0.04 / 0.01 | 0.008 / 0.000 / 0.000 |

Over several looks the chance of at least one fire is higher than any single cell. The live contrast also bundles a newer injury report
at T-30 (FORWARD section 2, "Bundled information"), which can only push the honest estimate lower. Reading: an honest fire is likely
if T-30 announcements capture most late scratches, and unlikely if they capture few. A fire alone therefore no longer separates "leak"
from "the announcement is as good as the box score". A pts estimate below the headline CI lower end (-0.0498) still does.

**Recommendation (a new DECISIONS row, not an edit to the frozen rule; Devin decides).** Keep the frozen thresholds. Fix in advance
what the audit they trigger consists of, and how a clean audit is read (drafted below).

---

## Findings, severity-ranked (each with the exact place and the fix)

| id | sev | where | finding | fix (recommended; not applied by me) |
|---|---|---|---|---|
| L1 | major | docs/TEST_LEDGER.md line 772 (T522), reading column | "real and large under every arm, including the served-mismatch arm" is false for P(play): RT_P2S - R is +0.0258 / +0.0357 on all rows. It is also false for late-resolved team-games, where P2S pts is +0.0319 / +0.0232. | Append annotation row A2 (below); do not edit T522 |
| L2 | major | reports/lineups_known_rebase/results.json `reading.flags.tripwire`; T522 | Tripwire risk judged on point estimates only. reb's -0.018 lies inside its honest headline CI, so it can fire on honest data (P about 0.46 if the true effect is the headline). | DECISIONS row D1 (below); annotation A2 names reb |
| W1 | major (provenance; not verdict-moving) | data behind WORKLOAD's 22 columns, 2024 blocks 10-17 | B refit is not equal to cached T452 B on 14,905 rows (2024 only) while P0C and FULL are bit-identical. An unexplained change between 2026-10-11 and 2026-10-27 in some of the 22 columns; it moves B - HEADS 2024 from -0.0006 to -0.0019. | Maintainer: diff the 22 `wl_*` columns' 2024 values against the earliest data snapshot after 2026-10-11 (or rebuild them from a 10-11 manifest) and name the column. Record it in A1. Required before any future WORKLOAD-column use; not required for this null |
| W2 | major (ledger wording) | docs/TEST_LEDGER.md line 771 (T521) | "T452's INVALID label is replaced by this valid null on the 22-column increment" conflates two questions. The rule (five-line summary item 5; Descriptive section) re-judges T452 only through B - PL27, and that reading is "T452's gain holds under a valid placebo and is recent-role minutes". | Append annotation row A1 (below) |
| L3 | minor | docs/prereg/LINEUPS_KNOWN_REBASE.md Amendment 1; T522 | Rule hash changed after the freeze, same id (a48ef7b3 to 876e5804). Pre-fit and pins only; T522 cites only 876e5804. Typed time "19:4x CDT". | A2 cites both hashes and commits. Future pin repairs take a new id or a `-a1` suffix |
| L4 | minor | T522 cells | N4 quoted as 2023 all-rows (-0.0196 / -0.0021) while the reading rule weighs bench-tonight in both seasons; P2S omitted; the CI column reads "in results.json" (non-negotiable 3). | A2 carries n and CIs |
| W3 | minor | research/eval/workload_eval.py line 361 (`"mde": (hi - lo) / 2`) | "MDE" is the CI half-width (about 50% power), not an 80%-power MDE (about 1.43 x larger). | Rename the field to `ci_halfwidth` or report 2.8 x SE; label it in future rules |
| W4 | minor | reports/prereg_workload_pts_v2/, reports/prereg_workload_pts_v2.md, reports/lineups_known_rebase/, reports/lineups_known_rebase.md | Untracked while committed ledger rows T521 / T522 cite them. | Commit these four paths by explicit path |
| W5 | minor | both harnesses | CIs come from `workload_eval._ci`, not `nba/truth/bootstrap.py`, which does not exist yet. | Known pending item; no action here |
| V1 | minor (environment) | `.venv` | `.venv/bin/python -m pytest` reports "No module named pytest". The suites ran under `.venv-1`. This is the "verify the verifier" pattern. | Install pytest in `.venv`, or document `.venv-1` as the test venv |

## The single most likely failure mode if a claim here is wrong
For LKR: **the live T-30 effect sits near the low end, not the headline.** If T-30 announcements resolve few late scratches, the live
pts gain is about -0.020. In late-scratch games it is a loss against production (P2S late +0.023, CI excludes 0), and the
T-30 P(play) idea is a harm (+0.036). The planning range already covers this. The risk is that the "large under every arm" sentence
gets read as "the live arm is safe everywhere". For WPV2: the unexplained 2024 change to the workload columns. If it were a
leak-shaped backfill it would inflate WF - FULL, but that only pushes a null toward a gain, so it cannot have produced this null.

## Not verified
* The extra-day lag test (both items): needs refits.
* Which of the 22 workload columns changed for 2024 (W1).
* That no arm was fitted under LKR's original hash a48ef7b3 before Amendment 1.
* Missingness of `t30_*` on the P(play) frame by played / DNP: the cache stores no features. Checked only on props rows (0.49 pp).
* A heads-level plant for LKR.
* The decision score (WPV2 `decision_score_dk_t60`). It was descriptive and is not read here.

## Playbook check (docs/MODEL_PLAYBOOK.md)
* Class: gradient-boosted trees (LightGBM context-residual centre and scale heads) read raw or through the lower-tail mixture
  (quantile layer). For N4, a LightGBM binary P(play) classifier.
* Information or capacity: WPV2's 22 columns are claimed as information, and the result says it is the five recent-role columns
  only; the rest re-express the base (the GBM check "does the arm only re-express the base?" is answered yes). LKR is information,
  measured through a proxy (box-score starters) whose upward bias is concentrated in late-resolved team-games.
* Enough data: yes for trees (about 27.6k / 33.1k rows per season; 2023 trains on about one season, which is noted, but both seasons
  agree).
* Test validity: real tips, integer support for every arm, game-clustered paired bootstrap, fair comparators (FULL / R hold recent
  minutes), past-only placebo (D2 fixed), reproduction bit-exact, plants pass. One validity caveat: the WPV2 PL27 placebo is not
  neutral (+0.0036 in 2023). It is conservative for B, so it cannot have produced a false pass.
* Lineage and inherited guards: WPV2 carries T452 / T467 guards per its table. LKR carries the red-team pess2 and adds P2S. The
  ancestor guard that matters (the proxy flag, F11) is exactly what P2S and pess2 bracket.
* Scored against: CRPS / log loss of the whole distribution, not a line or a market. No bet claim follows from either result.

## Commands (all exit 0 unless stated; read-only; season <= 2024 asserted in each script)
* `.venv/bin/python -W ignore <scratch>/wpv2_rederive.py`: exit 0, 12 s. Recomputes every WPV2 contrast, B - HEADS, the share, the
  drop-best-block replication, and cached-vs-refit B.
* `.venv/bin/python -W ignore <scratch>/n4_split.py`: exit 0. N4 split by DNP / played / surprise-DNP rows.
* Inline `.venv/bin/python -c` reads of both results.json files (late / outside decomposition; tripwire probabilities): exit 0.
* `git log` / `git diff 31c94e1 8fbf72c` / `git hash-object` on both harnesses: blob b6ac1daa (WPV2) and 2558c324 (LKR) equal the
  checkpoint stamps.
* `.venv-1/bin/python -m pytest -q -p no:cacheprovider tests/research/workload_pts_v2 tests/research/lineups_known_rebase`:
  **41 passed in 259.08s; exit 1** only because of the repo-wide 70% coverage floor on a subset run (0 failed).

## Drafted rows (not appended; the main session appends)
**Ledger annotation A1 (next free id), T452 / T521:**
`| T5xx | 2026-10-27 | ANNOTATION of T452 and T521 (adversary docs/reviews/wave1_post_result_2026-10-27.md) | pts integer CRPS, game-clustered; n 27,619 / 1,318 g (2023), 27,701 / 1,320 g (2024) | T452 re-judged with the past-only placebo: B - PL27 -0.0107 / -0.0100; honest size B - A0 -0.0071 / -0.0095 (T452 cached 2024 -0.0082); B - HEADS -0.0009 / -0.0019 | B - PL27 [-0.0147, -0.0067] / [-0.0139, -0.0065]; B - HEADS [-0.0038, +0.0020] / [-0.0042, +0.0005] | descriptive (-0.003) | game | n/a | **T452 is no longer INVALID: its gain is real under a valid placebo and is 80-97% the five recent-role columns (= FULL, T467); the other 22 add nothing (T521 is the valid null on them). PL27 is worse than A0 in 2023 (+0.0036 [+0.0005, +0.0068]), so B - PL27 overstates; quote B - A0. Open: the 2024 B refit differs from T452's cached B on 14,905 rows (an unexplained change to the 22 columns after 2026-10-11).** | no 2025 touch |`

**Ledger annotation A2 (next free id), T522:**
`| T5xx | 2026-10-27 | ANNOTATION of T522 (adversary docs/reviews/wave1_post_result_2026-10-27.md; rule frozen a48ef7b3 at 31c94e1, amended pre-fit to 876e5804 at 8fbf72c) | DESCRIPTIVE; game-clustered; props n 27,619 / 27,701, P(play) n 33,093 / 33,127 | N1 pts 2024 headline -0.0425 [-0.0498, -0.0358], pess2 -0.0209 [-0.0254, -0.0165], P2S -0.0203 [-0.0259, -0.0145]; N4 bench headline -0.0190 / -0.0220, pess2 -0.0015 / -0.0021, P2S +0.0013 / -0.0003; N4 P2S all rows +0.0258 [+0.0209, +0.0306] / +0.0357 [+0.0295, +0.0421] | as cells | n/a | game/date | n/a | **Correction to T522's reading: the served-mismatch arm (P2S) is NOT large under every arm. It harms P(play) on all rows and harms props in late-resolved team-games (pts +0.0319 / +0.0232, CIs above 0). The N4 gain is mostly the box-score proxy (72-94% on DNP rows; about 90% of the bench gain is gone under pess2). The whole headline-to-P2S props gap is the 17-18% of rows in late-resolved team-games. The FORWARD tripwire can fire on honest data for pts, ast AND reb (each threshold inside its headline CI).** | no 2025 touch |`

**DECISIONS row D1 (Devin decides; does not edit FORWARD_PREREG_2026_27):**
"FORWARD T30 direction check: what the triggered audit is. The frozen thresholds (pts -0.041, reb -0.018, ast -0.0089) sit inside the
honest integer-support, real-tip headline CIs from LINEUPS_KNOWN_REBASE (T522: pts [-0.0498, -0.0358], reb [-0.0208, -0.0147], ast
[-0.0115, -0.0076]). A fire is therefore not by itself evidence of a leak. The rule stays as frozen. When it fires, the audit it
triggers is: (1) every scored T30 row's lineup snapshot `known_at` < real tip - 30 min; (2) the `t30_*` path reads only the lineup
snapshot and the pre-tip report, never box-score or player_game_stats fields of that game (planted post-tip box row leaves the T30 row
unchanged); (3) the T-30 injury report used is stamped by its printed time (BEST_PRACTICES 2026-10-15); (4) the open-decision share
(FORWARD 2.1 item 2) is reported beside the estimate. If (1)-(3) are clean and the estimate is no lower than the stat's headline CI
lower end (pts -0.0498, reb -0.0208, ast -0.0115), the audit records 'fired, clean, consistent with honest T-30 information' and use
proceeds under the rule's other conditions. Below that bound, or with any of (1)-(3) failing, it remains a suspected leak. Why:
LINEUPS_KNOWN_REBASE puts the honest single-look fire probability at about 0.45-0.65 if the live effect equals the headline. Reverse
if: a clean live log shows the estimate below the headline CI lower end, or the open-decision share shows announcements resolve few
late scratches (then the effect should sit near -0.020 and a fire is again presumptive)."
