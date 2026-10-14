# RAPM_RETEST: RAPM player value in injury-Elo, re-asked on rebuilt lineups (GATE RECORD, STOPPED AT THE POWER GATE; no rule drafted)

Status: **DRAFT gate record, 2026-10-14. NOT a pre-registration.** The power gate fails, so by the instruction "if the gate cannot reach the floor, say so and do not draft further" there is no decision rule, no sha256 to commit and no fit. Nothing about the re-test outcome was computed: no RAPM was fitted, no log loss was scored, season 2025 was not loaded, every connection was `read_only=True`. This file records what was measured and what Devin would have to choose to go on.

- **Advice or momentum:** Devin's override (2026-10-14: "the null audit's two RE-TEST WARRANTED items today"). Claude's standing view, stated before the measurement and confirmed by it: the prior that RAPM beats the box value is low (section "Priors"), and the test cannot see an effect of plausible size at the requested design.
- **Model class:** linear ratings with a lineup-based player value (RAPM, ridge) feeding a MOV-Elo plus 2-coefficient ridge injury model (`rung0_injury_elo` v2). Playbook classes "RAPM and lineup-based ratings" and "Ratings".
- **Follows:** `docs/reviews/null_validity_audit_2026-10-14.md` section 3 rank 2 ("power check first"). Original: `docs/RAPM.md` (pre-registered 2026-10-08), ledger T176 (provisional back-fill, +0.0032 [-0.0003, +0.0066], n = 3,951 games, row-clustered = game-clustered).

## The question, restated
Original (frozen 2026-10-08): does a regularized adjusted plus-minus value, built from 5-on-5 possessions, replace the box-composite player value in the injury-adjusted MOV-Elo? Variant (b) RAPM value vs (a) production box value, same frozen MOV-Elo parameters, same pre-tip official-report rule, same monthly walk-forward refit of the two injury coefficients, log loss (b minus a), KEEP iff the paired game-level 95% CI lies entirely below 0 and no rotation-OUT slice (0, 1, 2+) is worse by more than 0.005. "RAPM-weighted roster strength accounting for who is OUT tonight" is exactly variant (b): `value = max(rapm, 0) * as_of_minutes / 48` summed over the OUT (and 0.5 x DOUBTFUL) players, differenced away minus home.
Re-test change: only the data. T176 read `possessions.off_players/def_players` from the v1 tracker (offence five equal to the v2 tracker's on 80.2% of possessions, i.e. different on about 1 possession in 5) and from possessions in `action_number` order (audit D2, D3). Both were fixed on 2026-10-09 and 10-10.

## Gate G-P (power). Definition set before the numbers were computed
Test: paired per-game log-loss difference d_i = loss_b,i - loss_a,i, scored games, game-clustered (one game = one row). SE = SD(d) / sqrt(n). The original rule has no point floor beyond "CI upper < 0"; the playbook's plausible effect for a ratings change is about 0.002 log loss, and the whole production injury term is worth -0.00812 vs MOV-Elo (T128, T091; n = 3,951). The gate: **the test must be able to return KEEP**, meaning
1. MDE (1.96 x SE) no larger than the whole injury-term gain 0.00812 (otherwise KEEP is impossible unless RAPM does better than all injury information), and
2. the true effect that gives 80% power (2.80 x SE) no larger than half of the injury-term gain (0.0041; a replacement of the player value cannot plausibly be worth more than half of what the whole availability signal is worth), and
3. P(KEEP | true effect -0.002) >= 0.50.
Any failure = the gate fails, no rule is drafted. (Choice protects against: freezing a test that can only say "inconclusive" and then reading its null as a finding, which is the T176 / validity-audit complaint.)

SD(d): not re-measurable without fitting. It is taken from the recorded T176 interval (`data/rapm/results.json`, `variants.b_rapm_value.vs_production.log_loss`): SE = (0.006618 - (-0.000304)) / 3.92 = 0.001766 at n = 3,951, so **SD(d) = 0.1110**. Caveat: that run is the pre-v2 data version; if the rebuilt RAPM sits closer to the box value (corr 0.69 then), SD(d) falls. Sensitivity row below halves it to 0.0555 (the rebuilt value would have to be about half as far from the box value, in logit terms, as the broken one was).

| design (n games, source) | SE | MDE 1.96 SE | effect with 80% power | as % of the 0.00812 injury gain | P(KEEP given true -0.002) | P(KEEP given true -0.004) |
|---|---|---|---|---|---|---|
| select 2023 alone (n 1,318, `nba.duckdb`) | 0.00306 | 0.0060 | 0.0086 | 105% | 0.10 | 0.26 |
| report 2024 alone (n 1,320) | 0.00305 | 0.0060 | 0.0086 | 105% | 0.10 | 0.26 |
| 2023 + 2024 pooled (n 2,638) | 0.00216 | 0.0042 | 0.0061 | 75% | 0.15 | 0.46 |
| 2022-24 pooled, the original (n 3,951) | 0.00177 | 0.0035 | 0.0050 | 61% | 0.20 | 0.62 |
| 2019-24 pooled, history scored too (n 7,595) | 0.00127 | 0.0025 | 0.0036 | 44% | 0.35 | 0.88 |
| (SD halved) select 2023 alone | 0.00153 | 0.0030 | 0.0043 | 53% | 0.26 | 0.74 |
| (SD halved) 2019-24 pooled | 0.00064 | 0.0013 | 0.0018 | 22% | 0.88 | 1.00 |

Script: scratchpad `rf_power.py` (formula only: normal approximation, z = 1.96 and 0.84). n for 80% power at -0.002 is 24,172 games; at -0.004 it is 6,043 games (SD 0.111). The series holds 15,000-odd games of any kind and 7,595 with a usable report.

**Result: G-P FAILS at the requested design (select 2023 / report 2024, per-season n about 1,318).** Item 1 passes (0.0060 <= 0.00812); item 2 fails (105% of the injury gain vs 50% allowed); item 3 fails (0.10 vs 0.50). The rule cannot be drafted for that design. It also fails for the original pooled 2022-24 design (item 2: 61%, item 3: 0.20) and for 2023 + 2024 pooled (75%, 0.15). Only the 2019-24 pooled design passes item 2 (44%) and still fails item 3 (0.35).

Honest bounds on what the numbers say: they are a normal approximation on one recorded SD, not a simulation, and they assume the paired differences are independent across games (clustered by game is the unit; same-day correlation is not modelled). They do not say RAPM is worse; they say a single season cannot tell.

## Data-capture audit (non-negotiable 8: the gate fails on data only after this)
Every season and source we hold, and every repairable gap, with cost. All queries `read_only=True`, `season <= 2024`.

| source | seasons | games | real tip | usable report at tip-60 | possessions with a full 5v5 | stints vs box (share of player-games within 1 min) | admit? |
|---|---|---|---|---|---|---|---|
| `nba.duckdb` | 2022 / 2023 / 2024 | 1,320 / 1,318 / 1,320 | 100% | 1,318 / 1,317 / 1,317 | 100% (263,457 / 260,449 / 262,152) | 99.98% / 99.97% / 99.98% | yes (select / report) |
| `data/history/nba_history.duckdb` | 2019 / 2020 / 2021 | 1,143 / 1,171 / 1,323 | 100% (`data/history/schedule`) | 1,141 / 1,170 / 1,323 | 100% (230,560 / 233,045 / 260,845) | 98.9% / 98.7% / 99.4% | yes as scored seasons (needs a design change) and as the RAPM prior |
| same | 2013-2018 | 1,309-1,319 each | none held | 0 (no reports held) | 100% | 100% | possessions only: a RAPM prior, not scoreable |
| 2018-19 season (label 2018) | | 1,312 | not held | **repairable gap**: official PDFs exist from at least 2018-12-31 (web search hit on `ak-static.cms.nba.com/referee/injury/Injury-Report_2018-12-31_01PM.pdf`, start of archive NOT verified); free; the history fetch ran 707 dates / 5,502 PDFs at 0.5 req/s, so about 110 dates is roughly 30 min plus parser work | 100% | 100% | adds at most about 750 games (n about 8,350, MDE 0.0024): does not change the verdict |

Conclusion of the audit: the capture is complete for what exists. The most data that can be scored with real-tip report gating is 7,595 games (2019-24), 8,350 with the 2018-19 repair; the power table's last row is the ceiling, and it does not reach a 0.002 effect with 50% power. The lineup fix itself is verified present (stints-vs-box reconciliation 99.97% vs 48.1% when T176 ran).

## A harness defect found while reading (not part of the gate)
`research/eval/rapm_injury_elo_eval.py` (the T176 entrypoint) calls `latest_pretip_flagged(rows, base_cfg)` on rows from `load_report_rows` with no `tip_et` and a `ReportTriggerConfig` that defaults to `tip_source="real"` since 2026-10-12. By the code read (`usable_report_rows` raises "tip_source='real' needs a tip_et column") it would stop with a ValueError today; and if it were patched to the proxy it would gate the RAPM arm on the 19:00 ET proxy while the box arm (`build_injury_features`) gates on real tips, an unfair pair (audit D1, BEST_PRACTICES "fair baseline"). Not run to confirm (it would load possessions). Any future RAPM test must build both arms from one flagged-set object. This does not change the gate.

## Options for Devin (nothing drafted)
- **O1 (recommended): close T176 as "untestable at this n", not as a null.** The audit's own words: the lineup error biased RAPM toward the team mean; the re-test cannot see anything smaller than 0.0060 per season. Record: "RAPM-in-injury-Elo: the old negative is not citable, the new test is not powered; no live arm". Cost: zero.
- **O2 (override): the largest honest design, pooled 2019-24 scored, RAPM trained from 2019 (history as prior per DECISIONS 2026-10-12 16:28), practical floor -0.004, select-then-report replaced by a pooled game-clustered CI.** n = 7,595, MDE 0.0025, 88% power at a true -0.004, 35% at -0.002. This is NOT "the same design except the data" (it adds scored seasons and drops select 2023 / report 2024), so it is a different rule id and needs Devin's words and a new gate (era flags for the bubble and 2020-21; 2021-22 hourly report cadence is not in the scored set). Cost: about one hour on the Mac CPU per the audit (the original run took about 6 s wall for 786,814 possessions and 26 monthly refits at 0.03 s; 2019-24 is about 2x the possessions), plus writing and freezing the rule and a new gate. My prior that it returns KEEP is 0.04.
- **O3: the 2018-19 report repair** adds 750 games and changes nothing in the verdict; not worth doing for this question.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: RAPM and lineup-based ratings; ratings (injury-Elo) as the host model.
1. **Information or capacity?** Information-quality: the old RAPM read wrong lineups. But the incremental information over the box value is tiny in the old run: variant (c) both features gave -0.00004 [-0.00038, +0.00028], i.e. the box value already carries what RAPM carried, to within 0.0003. A better lineup could change that; it cannot be assumed.
2. **Enough data for the class?** RAPM is data-hungry. Ratings (Elo) are not. The pre-v2 season-to-season RAPM correlation was 0.34-0.36 on single seasons. The data now exist (2019-24 with prior from 2013) but the evaluation window is where the data shortage sits: the injury-Elo log-loss comparison has about 1.3k games per season.
3. **Test valid?** The old one was invalid (design matrix defect). The proposed one is invalid as a decider: power failure (above). An unpowered test is not a null either, so the old result stays uncitable in both directions.
4. **Lineage and inherited guards:** T176 rule carried (variants a/b/c, slice veto 0.005 in the original). Not carried: the harness (see defect). Guard to add in any future version: both arms built from one flagged-set object; planted-future test on the RAPM snapshot (a snapshot labelled c uses possessions strictly before c; exists in `tests/features/test_rapm.py`).
5. **Scored against:** production `rung0_injury_elo` v2 and plain MOV-Elo on log loss and Brier, not the market. The market beats both on game winners (CLAUDE.md); nothing here changes that.

## Priors (drafted for PREDICTIONS_LEDGER; not appended)
`| 2026-10-14 | RAPM_RETEST (gate record) | modeler: P(power gate G-P passes at select 2023 / report 2024) 0.03 (stated before rf_power.py ran; the 0.0060 per-season MDE was derived from the audit's 0.0035 by sqrt(3951/1318)); measured: fails (MDE 0.0060, 80%-power effect 105% of the injury gain). If Devin overrides to O2: P(KEEP) 0.04, expected pooled dLL +0.001 (80% -0.003 to +0.005), P(INCONCLUSIVE) 0.50, P(REJECT, CI lower > 0 or slice veto) 0.46 | docs/prereg/RAPM_RETEST.md | gate: fails; O2 pending | |`

## Not verified
- SD(d) on the rebuilt lineups (needs the fit); the power table uses the pre-v2 SD and a halved sensitivity row.
- That the 2018-19 report archive starts where the web search suggests; that its layout parses with the existing parser.
- The ValueError claim for the T176 harness (read from code, not run).
- The history reconciliation (98.7-99.4%) comes from the F12 check-1b code run on the history DB (same quantity: stint minutes Q1-OT vs box within 1.0 min); it says the lineups reconcile with box minutes, not that every five-man unit is right (no independent lineup source is held).
