# Pre-freeze review: AVG_SHADOW_LIVE_2 (DRAFT, commit 78486ad), 2026-10-14

Reviewer: adversary. Object: `docs/prereg/AVG_SHADOW_LIVE_2.md` (DRAFT, no sha), which supersedes the frozen
AVG_SHADOW_LIVE (sha 6e57fc11) on one point: guard G1 is read on the stored 199-point pmf (`avg_pmf199`) instead of
the 19 stored quantiles read as equal-mass atoms, and proof P3 rehearses that. No live row exists; every number here
is from seasons <= 2024 (asserted in every script). Season 2025 was not loaded. No DuckDB write connection was opened.

**Adversary verdict on the change: SURVIVES.** Reading G1 on the 199-point form repairs a test that was invalid.
It is not a search for a form that passes. Three things support this. First, a perfectly calibrated integer forecast
loses about three quarters of the 0.02 tolerance on the 19-atom reading. Second, the 19-atom wording in the frozen
rule came from a mislabel in this reviewer's own B2 fix. Third, the draft picked the less favourable of the two
199-point constructions. The thin margin at q20 is real and stated. It is a risk to a PASS, not a flaw in the rule.

## Per-check table

| # | Check | Command (exit code) | n | Number (CI) | Result |
|---|---|---|---|---|---|
| 1 | Frozen rule sha | `git show 92ff960:docs/prereg/AVG_SHADOW_LIVE.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256` (0); same on HEAD (0) | - | `6e57fc11b248...` both | PASS: the frozen rule is unchanged since its freeze |
| 2 | Diff against the frozen rule, hunk by hunk | `diff frozen.md docs/prereg/AVG_SHADOW_LIVE_2.md` (1 = differences) plus a word-level diff of every changed line | 13 hunks | Hunks: 1c1, 3-5c3-5, 6a7-51, 24a70, 33c79, 41c87, 52c98, 69a116, 75-76c122, 77a124-126, 87c136, 100c149, 108a158-160, 110c162. No hunk falls on rule items 1-7 (frozen lines 8-14), on G2-G5, the placebo/shuffle/control, stamp, staleness, heartbeat, interim, counting, P1a/P1b/P2/P4, gate, cost or kill criteria. Every hunk matches a row of the diff table. Line 4 changes only the logging clause; the three uses are word-for-word the same. | PASS |
| 3 | Is the 19-atom G1 a valid test? A data-free toy with perfectly calibrated integer forecasts | `.venv/bin/python -W ignore scratchpad/toy_g1b.py` (0); NB counts, 5 configurations, 40,000 rows each, true integer quantiles | 40,000 x 5 | 19 atoms: q10 0.115-0.117, q20 0.212-0.215, cov80 0.772-0.780 (fails G1 in 2 of 5 configurations). 199 reconstruction `to_integer_support(ext19(q19))`: q10 0.100-0.102, q20 0.198-0.202, cov80 0.797-0.803. A continuous control with no ties: 19 atoms 0.103 / 0.200 | 19-atom form INVALID: it uses about 0.015 of the 0.02 tolerance on a perfect forecast. The 199 form is about unbiased. |
| 3b | Does the 199 form have power? | same script; forecast mean scaled 0.90-1.10 | 40,000 | scale 1.05: 199 q10 0.1095, q20 0.2166, fails; 0.95: 0.0868 / 0.1769, fails; 1.00 passes | PASS: G1 on 199 still detects a 5% mean error |
| 4 | Provenance of the frozen 19-atom wording | `grep -n 0.114 docs/reviews/AVG_SHADOW_LIVE_prefreeze_2026-10-13.md` (0) | 25,767 | The frozen rule's G1 rehearsal value, "randomised PIT 0.114, about 0.006 of slack", equals the offline 199-point reading (AVG 0.1144, A0 0.117 = `199off` in rehearsal.json). It is not the 19-atom reading (0.1257). The B2 fix in the 2026-10-13 review (lines 55-57) wrote "on the 19-atom pmf" next to a 199-point number. | The fork started as a reviewer mislabel. The draft does not say so (N1). |
| 5 | Was any other form tried? | `rehearsal.py`, `p3.json`, 2026-10-13 review row 3b | 25,767 | Forms seen: inclusive P(y<=q), strict, randomised PIT (all three on 10-13, before freeze); 19 atoms; avg_pmf199; offline 199. The adversary also read `to_integer_support(ext19(stored avg q19))`: q10 0.1110 [0.1072, 0.1145], q20 0.2136 [0.2087, 0.2184], P(subsample fail) 0.016 | PASS: the chosen avg_pmf199 is the LESS favourable 199-point construction |
| 6 | G1 on 2024 with a clustered CI | `.venv/bin/python -W ignore scratchpad/attack_g1.py` (0); game-clustered bootstrap, 2,000 draws, seed 0 | 25,767 rows, 1,230 games | avg_pmf199 q10 0.1129 [0.1091, 0.1166], q20 0.2151 [0.2103, 0.2200], cov80 0.7900, bias -0.086; 19 atoms q10 0.1257 [0.1216, 0.1295], q20 0.2272 [0.2222, 0.2321] | Reproduces the draft to 4 dp. The q20 upper bound touches the 0.22 limit. |
| 7 | In-sample check: 2023 replication (comparators only; AVG 2023 needs the OOF file that also holds 2025) | `.venv/bin/python -W ignore scratchpad/drift_2023.py` (0); lower-tail components, `row_season.max() <= 2024` asserted | 2023: 25,815 rows / 1,230 games; 2024: 25,767 / 1,230 | 199 reconstruction: FULL 2023 q10 0.1147, q20 0.2170 (pass); 2024 0.1161 / 0.2155 (pass). A0 2023 0.1154 / 0.2157 (pass); 2024 0.1165 / 0.2151 (pass). 19 atoms: every arm fails both seasons (q10 0.130-0.131) | PASS: the pattern repeats out of sample for the comparators. Drift 2023 to 2024 is about 0.0015. |
| 8 | P(G1 fails at the live final look) | analytic: 2024 game-clustered SE scaled to 10,470 rows | 10,470 (week 12, base case) | q20 SE 0.0039, margin 0.0049, P(fail) about 0.10; q10 SE 0.0030, margin 0.0071, about 0.01. Union about 0.11 if 2024 holds; about 0.19 at the 2023 comparator level (q20 +0.0015); about 0.22 with an extra drift sd of 0.005 | The rule's 0.85 sits at the optimistic end of 0.78-0.89. Line 50 quotes only the q10 SE (N2). |
| 9 | Consistency: scored object vs G1 form | rule lines 10, 53, 79, 87 | - | Every CRPS contrast, G2-G5, (c)-(g) stay on the 19 atoms for every arm. G1 is the only absolute (non-contrast) limit, and a reading bias does not cancel in an absolute limit. On a contrast it cancels: (c)/(f)/(g) and G2-G5 pass on both forms (rehearsal.json). | PASS. The rule should state the absolute-vs-contrast reason in one line (N3). |
| 10 | ITT rows without an AVG row read `to_integer_support(ext19(FULL q19))` | attack_g1.py | 25,767 | avg_pmf199 and the reconstruction of its own stored 19 differ on 19.3% of cells (every row has at least one difference). G1 therefore mixes two constructions. The ITT share is <= 5% (coverage gate), and both constructions pass (FULL reconstruction q10 0.1140, q20 0.2145), so the effect on G1 is <= about 0.0005. | PASS. Disclose the mix (N4). |
| 11 | FULL_SHADOW_LIVE has no guard that reads the 19 form as an absolute limit | `grep -n -i "calibrat\|coverage\|PIT\|atom\|199" docs/prereg/FULL_SHADOW_LIVE_2026_27.md` (0); `full_check_19.json` | 25,897 (2024 `002`+`005`) | No calibration or PIT guard exists. Line 29 uses the 19 atoms inside the scored joint mixture (a contrast, same form for both arms). 19 vs 199: FULL-A0 pts -0.00880 vs -0.00859, fit-inflated upper bound -0.0038 on both. Placebo, shuffle and slices keep their sign on both forms. | PASS |
| 12 | Holdout | `grep -n "^| 72\|^| 73" docs/HOLDOUT_ACCESS_LOG.md` (0) | - | Row 72 (2026-10-13) covers the three uses word for word; it names AVG_SHADOW_LIVE only. Row 73 (2-row schema peek, disclosed after the read) is also inherited and is not cited. | PASS with N5 |
| 13 | Integer support | rule line 53 | - | All arms are scored with the same 19-atom `integer_crps`. G1 compares no arms. | PASS |
| 14 | Code that must change before P3 can pass | `grep -n "calibration(" nba/eval/avg_shadow_live.py research/colab/jobs/avg_shadow_rebuild/p3_equivalence.py` (0) | - | `nba/eval/avg_shadow_live.py:473` `cal = calibration(a["AVG"], y)` reads the 19 atoms; docstring at :357 says "19-atom". `p3_equivalence.py:113` reads `q["AVG"]` (19 atoms). | Steward work after the freeze (draft lines 98, 136). It must land before counting starts (N6). |

Shuffled labels, planted future, knockout and missingness do not apply: no fit and no result exist, and the ITT and
missingness text (rule 2) is byte-identical to the frozen rule.

## Answers to the brief
1. **Forking paths.** The form was chosen after the 19-atom reading failed on 2024, and 2024 is also the rehearsal
   season, so the 2024 pass is in-sample. What makes the choice principled rather than a search: (a) the toy shows the
   19-atom reading cannot hold a perfect integer forecast near nominal, while the 199 reconstruction can (check 3);
   (b) the frozen 19-atom wording came from a mislabel, and the number that approved the frozen rule was the 199 reading
   (check 4); (c) 2023 replicates the result for FULL and A0 (check 7); (d) no tolerance moved; (e) of the two 199-point
   constructions, the draft picked the less favourable (check 5). A tolerance change (for example, centring the 19-atom
   limits on +0.015) would have been the real fork; the draft did not make it. What is served: the shadow serves
   nothing. If AVG is promoted, the served object is the 19 quantiles. G1 asks whether the forecast distribution is
   calibrated; the 19 quantiles are that distribution's summary, and the atom reading misstates them.
2. **Consistency.** The scored object is unchanged. Reading G1 on a different form than the CRPS is fine because G1 is
   the only absolute limit (check 9). The ITT construction is coherent and its effect is negligible (check 10).
3. **Diff.** Clean (check 2).
4. **Thin slack.** P(G1 fails at the final look) is about 0.10-0.22 (check 8). The rule should say q20 is the binding
   quantile and quote its SE (N2).
5. **FULL.** No guard reads the 19 form as an absolute limit (check 11).
6. **Holdout.** Row 72 covers the inherited uses; row 73 should be cross-referenced (N5).

## Findings
No blocking findings.

* **N1 (major, text).** Line 12, "The mechanism of the 19-atom shift is NOT established ...". Fix: replace it with
  the toy result (perfect NB forecast: 19 atoms q10 0.115-0.117 / q20 0.212-0.215; 199 reconstruction 0.100-0.102 /
  0.198-0.202; continuous control 0.103 / 0.200). Add: "the frozen rule's G1 number (0.114, 2026-10-13 review row 3b)
  was the offline 199-point reading, mislabelled 19-atom in that review's B2 fix". This is the strongest defence the
  draft has, and it is missing.
* **N2 (minor).** Line 50, "the row-level standard error of a coverage near 0.11 is about 0.003". Fix: "q20 binds:
  game-clustered SE about 0.0039 at 10,470 rows against a margin of 0.0049, so P(G1 fails) is about 0.10 if 2024
  holds and about 0.2 at 2023's comparator level". Lower the line 122 prior to 0.80-0.85 or give the range.
* **N3 (minor).** Line 70. Fix: add "G1 is the only absolute limit; every contrast keeps the 19 atoms on both sides,
  where a reading bias cancels".
* **N4 (minor).** Line 79, ITT clause. Fix: add "avg_pmf199 is the pre-compression mixture, not a reconstruction of
  avg_q19 (19% of cells differ); the reconstruction reads q10 0.1110 / q20 0.2136 on 2024, more favourable, and is
  not used".
* **N5 (minor).** Lines 4 and 125. Fix: also cite HOLDOUT_ACCESS_LOG row 73 (the schema-peek disclosure) as
  inherited.
* **N6 (minor).** Line 98, "a pass condition only for the stats the steward proposes (rule 7)". A pre-count proof
  should not depend on a later proposal. Fix: "printed; a count stat failing it here is not proposable under rule 7".
  Also: the scorer `nba/eval/avg_shadow_live.py:473` and `p3_equivalence.py:113` move to avg_pmf199 before P3 is
  re-run, with the fixture test from line 136.

## Single most likely failure mode if the claim is wrong
G1 is not the leak risk here; it can only block. The real risk is an honest AVG that fails G1 on q20 by chance at
the final look (about 1 in 10, up to 1 in 5). That would turn a real CRPS gain into no PASS, and a new rule id after
seeing it would be exactly the variant-chasing the project forbids.

## Playbook check (neural sequence model blended 50/50 with a gradient-boosted residual model; this review tests the calibration guard, a quantile/distribution-layer question)
1. Information or capacity: capacity (W_ALL data volume). This change adds neither; it fixes how a guard is read.
2. Enough data: unchanged from the frozen rule (W_ALL, six seasons). G1 needs n >= 3,000, reached by week 4 (base).
3. Test valid: the 19-atom G1 was invalid (a guard that fails, or nearly fails, a perfect forecast), and the playbook
   says an invalid test is not a null. The 199 form is valid and has power (check 3b). Distribution-layer check: the
   reliability is read at the 0.10/0.20 points that bind, not only through overall CRPS.
4. Lineage: T478's guards and `lower_tail_eval` read calibration on the 199 grid. The frozen rule's 19-atom wording
   was a review error. FULL_SHADOW_LIVE has no calibration guard.
5. Scored against: whole-distribution integer CRPS against FULL and A0 on 19 atoms (unchanged). G4 is a threshold log
   loss against A0. Not the line.

## Corrected family and power note
No family change: m = 1 primary, BH over the same 7 secondaries. G1 does not enter the BH family. G1 at the final
look: pass margin 0.0049 against SE 0.0039 at q20, so the guard has power against a mean miscalibration of about 5%,
and a chance of about 0.1 of blocking a well-calibrated AVG.

## Not verified
AVG's own 2023 calibration (needs the OOF file that also holds 2025; not opened). The live feed's effect on calibration.
The steward's post-freeze changes to the scorer and the P3 job. The toy uses NB counts; real pts have mass at 0 from
short minutes. It reproduces the observed 19-atom offset to within 0.01, but it is a model, not the data.

## Drafted rows (not appended)
PREDICTIONS_LEDGER (proposal): "AVG_SHADOW_LIVE_2 G1 at the final look: P(pass | otherwise readable) 0.80-0.88
(adversary, 2026-10-14; q20 margin 0.0049 vs SE 0.0039)."
AGENT_INCIDENTS (proposal): "2026-10-13 pre-freeze review B2 fix labelled a 199-point G1 reading (0.114) as 19-atom;
the frozen rule inherited the label and P3 failed on it. Rule: a fix that names a form must quote the number computed
on that form, with the command."

Verdict: APPROVED
