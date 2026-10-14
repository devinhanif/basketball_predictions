# F12_RETEST: closing-lineup risk re-asked on rebuilt stints (GATE RECORD, STOPPED AT THE FROZEN PREMISE CHECK; no rule drafted)

Status: **DRAFT gate record, 2026-10-14. NOT a pre-registration.** The frozen F12 minimum data checks were re-run unchanged on the current stints. The stint defect is gone (check 1b passes at 99.97%), but the premise check (2b) fails by the same margin as in T185, so by the frozen rule ("Check 2 fails (premise not replicated) ... close") there is nothing to re-test and no rule is drafted. No `cl_*` column was built, no arm fitted, no CRPS computed; season 2025 not loaded; every connection `read_only=True`.

- **Advice or momentum:** Devin's override (2026-10-14: the audit's two RE-TEST WARRANTED items today). The audit ranked this first because "the gate answer certainly changes at 1b; the premise is unknown". Both halves are now measured.
- **Rule re-tested:** `docs/prereg/F12_CLOSING_RISK.md`, frozen sha256 **484e35b7** (the full hash `484e35b7f0202918050d988c4b0415505b622625b1e19d28f52024fdeb8176f0` was recomputed from the file at this branch's HEAD with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' ... | shasum -a 256` and again inside `nba.eval.f12_closing_risk.frozen_sha256()`; both equal the frozen value, so the rule text is unchanged since T185). Original result: ledger T185 (checks 1b FAIL 48.1% of 82,403 player-games; 2b FAIL -31.3%).
- **Model class:** descriptive gate (no model). The thing the rule would fit later is a feature group for the short-minutes classifier of a mixture (gradient-boosted trees); playbook class "Gradient-boosted trees".

## Gate re-run (the frozen checks 1-2, same code, same cut-offs; seasons 2022-24)
Command (read-only; output redirected away from `reports/prereg_f12/`, which holds the T185 record and must not be overwritten):
a three-line script calling `nba.eval.f12_closing_risk.run_checks('nba.duckdb', <scratch dir>)` from the repo root (scratchpad `rf_f12gate.py`; exit 0; do NOT use the module's `checks` stage as is, it writes `reports/prereg_f12/results.json`). Count: 84,180 player-games joined to box rows (T185 had 82,403 on 2026-10-09; the +1,777 is most likely the short-appearance stints F4 / F4b restored plus the 5 restored neutral-site 2024 games; not decomposed).

| check | frozen threshold | T185 (2026-10-09, v1 stints) | now (v2 stints, `nba.duckdb`) | pass now |
|---|---|---|---|---|
| 1a stints cover games | >= 95% | 100% | 100% (3,958 / 3,958) | yes |
| 1b Q1-OT stint minutes vs box within 1.0 min | >= 95% of player-games | **48.1%** | **99.97%** (84,180) | yes |
| 2a vets (draft idx >= 5): close vs blowout ratio gap | >= +4% | +32.5% (.336 n 10,093 vs .253 n 13,161) | +35.0% (.335 n 10,126 vs .248 n 13,369) | yes |
| 2b young (idx <= 2): close vs blowout gap | within +/-4% | **-31.3%** (.319 n 3,065 vs .465 n 5,469) | **-32.4%** (.320 n 3,087 vs .474 n 5,731) | **no** |

Ratio = pooled Q4 floor minutes over Q1-Q3 floor minutes (baseline 0.333), close = final margin <= 5, blowout >= 15, idx = season - draft_year + 1 (drafted players only), exactly as the harness has it.

**Reading.** The defect was real (check 1b) and is fixed. It did not cause the premise failure: with stints that reconcile to box minutes at 99.97%, young players still play far more of the fourth quarter in blowouts (.474) than in close games (.320, about the 0.333 baseline). That is garbage time: veterans are rested in blowouts (.248), the young are not. Devin's descriptive ".333 close vs .341 blowout" for young players is not reproduced on any stint version.

## Data-capture audit (non-negotiable 8: a gate fails on data only after this)
The same frozen check 2 was run on every season held, including `data/history/nba_history.duckdb` (2013-2021; `rf_f12hist.py`, descriptive, exit 0). Pooled history: 1a 99.99%, 1b 99.68%, vets +30.6%, young **-33.5%** (close .330 n 10,017, blowout .497 n 14,784). Young-player gap by season (close vs blowout):

| season | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| young gap | -36.6% | -33.6% | -35.9% | -43.9% | -30.5% | -30.3% | -23.4% | -36.9% | -30.2% | -28.9% | -33.1% | -34.6% |
| vet gap | +28.4% | +22.4% | +24.4% | +30.3% | +27.0% | +36.7% | +30.3% | +45.3% | +34.5% | +29.3% | +35.7% | +39.7% |

All 12 seasons fail 2b (never inside +/-4%, never above -23%); all 12 pass 2a. Stints vs box reconcile at 100% (2013-18), 98.9 / 98.7 / 99.4% (2019-21) and 99.98 / 99.97 / 99.98% (2022-24). Sources: stints are derived from the play-by-play we hold; there is no second lineup source. Repairable gaps: none that bear on this check (the check reads stints, box minutes, final score and `draft_year`; `draft_year` is the only static column, and 2b fails on 24,801 young close or blowout player-games in the history seasons alone). So the failure is a data finding, not a capture failure.

## What this means and what I did not draft
- The frozen rule says: check 2 fails, close; "at most ONE pre-registered follow-up, then closed"; and T185 added "no follow-up with other cut-offs". The re-test the audit asked for was the same rule on repaired data; it has been run to its first stopping check and closed on the data it was meant to be repaired by. **F12 is now closed validly (premise not replicated on valid stints), where T185 closed it on a defective gate.** The ledger row should say so: T185's 1b failure is superseded; its 2b failure is confirmed on v2 stints and on 2013-2021 history.
- A rewritten premise (per-game mean-of-ratios as in the doc's `cl_cs_*` definition, garbage-time excluded, close games only, or a different young cut-off) would be a **different rule id and a variant** (the ledger caveat on T185 names the definition gap; the audit suggested "garbage time defined before running"). I did not draft it: it is exactly the variant-chasing the kill criteria forbid, it was not asked, and the 2a/2b design (young "within +/-4%") expects the opposite of what the data show. If Devin wants the young-player closing question asked in a form the data can answer (e.g. "does a young player's pre-tip P(close) change his minutes lower tail", without the premise gate), that is a new hypothesis with its own gate and his words, not a re-test.
- Cost of the one thing that would otherwise be next: none; the study stops.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: descriptive gate; host class gradient-boosted trees (mixture classifier).
1. **Information or capacity?** Information-quality gate only (is the stint data right). Verified: 99.97% reconciliation. The premise is about information the data do not support.
2. **Enough data for the class?** Not at issue: 3,087 young close-game player-games in 2022-24, 10,017 in history; the gap is stable at -23% to -44% in every season.
3. **Test valid?** T185's 1b was invalid (defect), its 2b was valid in principle but read the same stints. Now both valid. Remaining weakness stated: ratios are pooled sums, not per-game means; the original harness chose pooled and the frozen text does not decide between them. Direction: pooled sums weight high-minute players; the per-game definition was not computed (it would be a variant).
4. **Lineage and inherited guards:** F12 rule carried unchanged (sha 484e35b7); checks 3-5 (missingness, planted same-game, B0/M1 reproduction) were not reached; premise gate is the first stop. LOWER_TAIL / MINUTES_V2 lineage untouched.
5. **Scored against:** nothing; no arm scored.

## Priors (drafted for PREDICTIONS_LEDGER; not appended)
Honest process note: I did not write a prior for this gate before running it. The row below is stated after the gate result and is **marked not-for-calibration**. Forward: if Devin asks for the premise-free variant above, P(pts CRPS gain <= -0.003 with CI upper < 0) 0.05 (a pre-tip feature of margin closeness is already in `abs_margin`, `min10`, `starter10`; the audit and the F11 / F8 lineup nulls give the same prior).
`| 2026-10-14 | F12_RETEST (gate record) | modeler (STATED AFTER the result, not for calibration): P(check 1b passes on v2 stints) 0.99 (measured 99.97%); P(check 2b passes) 0.10 (measured: fails, -32.4%, and in 12 of 12 seasons 2013-24) | docs/prereg/F12_RETEST.md | gate: 1b yes, 2b no; closed | n/a (post-hoc) |`

## Not verified
- The per-game mean-of-ratios definition of the premise (not computed; it is a variant).
- Whether the history seasons' `draft_year` coverage biases the young group (history `players_static` was empty before 2026-10-12 20:34 and filled afterwards; DECISIONS, audit D12); the history table above is therefore a replication, not a reference.
- The 2013-18 history stints were built by the same pipeline as 2019-21; their reconciliation (100%) is as reported, not independently checked.
