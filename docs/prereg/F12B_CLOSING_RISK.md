# F12B_CLOSING_RISK: the closing-lineup premise re-measured on rebuilt stints with garbage time excluded (pre-registration DRAFT, descriptive)

Status: **DRAFT 2026-10-27, NOT frozen.** Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence would be
the commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F12B_CLOSING_RISK.md | shasum -a 256`.
No premise ratio with the garbage-time exclusion has been computed, no `cl_*` column built, no arm fitted, season 2025
never loaded, every connection `read_only=True`.

- **Advice or momentum:** Devin's request (DECISIONS 2026-10-27 18:32, wave 1: "F12B_CLOSING_RISK descriptive (T185)").
  Flag: on 2026-10-14 (docs/prereg/F12_RETEST.md) the modeler declined to draft exactly this variant without Devin's
  words, calling it variant-chasing under F12's kill criteria. Devin's request supplies the words; under F12's own kill
  criteria ("at most ONE pre-registered follow-up, then closed") **this is that one follow-up**. If it fails, F12 is
  closed for good, with no further cut-off, definition or season variants.
- **Model class:** descriptive gate (no model). The thing a later rule would fit is a feature group for the
  short-minutes classifier of a mixture (gradient-boosted trees); playbook class "Gradient-boosted trees".
- **Re-tests:** T185 (INVALID: check 1b and the premise both read pre-v2 stints). Redo rank 8, validity audit
  2026-10-12 section 3: "descriptive re-measure of reconciliation and the premise on the rebuilt stints with a defined
  garbage-time exclusion; 2023/24; F12b only if it passes."
- **Lineage:** docs/prereg/F12_CLOSING_RISK.md (frozen sha256 484e35b7), T185; F12_RETEST gate record (2026-10-14:
  1b passes at 99.97%, premise 2b fails -32.4% pooled 2022-24 and in all 12 seasons 2013-24 WITHOUT any exclusion).

## What is already known (data exposure, honest)
Seen: F12_RETEST's pooled ratios with NO exclusion (young close .320 vs blowout .474; vets .335 vs .248) and its
per-season young gaps (2023 -33.1%, 2024 -34.6%) and vet gaps (+35.7%, +39.7%). That gap is plausibly garbage time
(the young play the end of blowouts). Not seen: any ratio with garbage time removed, any per-season CI, the per-game
mean-of-ratios version.

## Hypothesis (Devin's premise, FAN_KNOWLEDGE round 4)
Outside garbage time, coaches give veterans more of the fourth quarter in close games than in blowouts, and young
players about the same share in both. H0 for the young: the gap is outside +/-4% (the premise is stale even when
garbage time is removed). What each piece protects against: the exclusion stops a mechanical artefact (bench players
finishing decided games) from standing in for the coaching behaviour the premise is about; requiring both seasons stops
a one-season fluke from opening a model rule.

## The only changes from frozen F12 check 2
1. **Garbage-time exclusion** (defined from game state only, before any ratio is computed): a fourth-quarter
   possession is garbage iff its START margin `m = |score_diff - pts|` (offence perspective, `possessions`) meets
   `m >= 25` with clock > 540 s left, `m >= 20` with 360 < clock <= 540, or `m >= 10` with clock <= 360. The garbage
   state holds from that possession's `clock_start` until the next possession's `clock_start` (the last one until
   0:00); **amendment A1 (2026-10-27, before any premise number, see "Gate result")**: the FIRST Q4 possession's state
   holds from 720 s, since possessions start at their first event (median 704 s) and that possession's start margin is
   the end-of-Q3 margin. The thresholds are the clock / margin tiers of the public Cleaning the Glass definition; its extra "two or
   fewer starters on the floor" condition is NOT used, because it would select on who is on the floor, the quantity
   being measured. Overtime is excluded as in F12.
2. **Normalisation for the removed time.** For player-game i in game g: `q4ng_i` = his Q4 floor minutes outside
   garbage, `A_g` = 12 minus game g's Q4 garbage minutes. Cell ratio
   `R = (sum q4ng_i / sum A_g) / (sum q13_i / (36 N))`, pooled over the N player-games in the cell (baseline 1.0; with no
   garbage this equals F12's pooled `q4 / q13` times 3). Player-games with `A_g < 1` minute are dropped from the cell
   (their Q4 share is undefined). Gap = `R_close / R_blow - 1`.
3. **Per season, 2023 and 2024 separately** (F12 pooled 2022-24). 2022 is not used (warm-up season elsewhere; keeps
   the scored seasons the same as the rest of wave 1).
Everything else is F12's: close = final |margin| <= 5, blowout >= 15; veteran = `season - draft_year + 1 >= 5`, young
`<= 2`, drafted players only (undrafted excluded, as the F12 harness did); every game in `games` for the season
(regular season and playoffs, as the F12 harness did); stints from `stints`, box minutes from `player_game_stats`.

## Inference
Game-clustered bootstrap of each gap (B 2,000, seed 0; resample games, recompute both cell ratios), 95% percentile CI,
reported per season. The decision is on the point estimate, as F12's check 2 was; the CI says how far to trust it.
No multiplicity correction: this is a stopping premise, not a claim; nothing is promoted on it.

## Decision rule
- P1 (veterans): gap >= +0.04 in 2023 AND in 2024.
- P2 (young): |gap| <= 0.04 in 2023 AND in 2024.
- **PASS** iff P1 and P2 both hold in both seasons. PASS earns only a NEW pre-registration (F12B_MODEL: the `cl_*`
  feature group for the short-minutes classifier) with its own minimum-data and power gate; nothing is fitted under
  this id. Warning stated now: the F12 floors (pts integer CRPS -0.003, minutes CRPS -0.03 min) are likely below the
  resolution of a whole-population pts test (T506: pts half-width 0.0055-0.0074 at n ~25,700 rows per season), so
  F12B_MODEL may itself stop at its power gate; minutes would then have to be the primary target.
- **FAIL** otherwise: F12 closed permanently ("premise not replicated on valid stints, garbage time removed").
- Descriptive only, cannot rescue a FAIL: the per-game mean-of-ratios version (the `cl_cs_*` definition), the
  no-exclusion ratios for comparison, young starters, and the young/vet split at idx 3-4.

## Minimum-data gate (label-free with respect to the premise; must pass before freeze; non-negotiable 8)
Written before the gate script ran. On data_version `49a2fc3d3dff` (manifest 2026-10-27 12:09 UTC), seasons 2023 and
2024, each separately.
- G1a stints cover >= 95% of games; G1b stint minutes (Q1-OT) within 1.0 min of box on >= 95% of player-games (frozen
  F12 check 1).
- G2 the possession state covers the fourth quarter: share of games whose first Q4 possession starts at >= 700 s and
  whose possessions carry a non-null `score_diff` >= 98%. (As written this FAILED on a mis-set bar; see A1 below. The
  amended check G2-A1: first Q4 possession starts at >= 680 s and `score_diff` non-null, >= 98%, with the state carried
  from 720 s.)
- G3 the state margin agrees with the result: share of games whose last possession (any period) ends within 3 points of
  the final |margin| >= 95% (the start-margin arithmetic is right).
- G4 cell counts: each of the four cells (young / vet x close / blowout) holds >= 500 player-games and >= 150 games
  after dropping `A_g < 1`; total non-garbage Q4 minutes available in the young x blowout cell >= 1,000.
- G5 precision: the projected 95% half-width of the young gap <= 0.04. Projection: the game-clustered bootstrap
  half-width of the young gap WITHOUT exclusion (its point estimate is already known from F12_RETEST; only its spread
  is new), inflated by `sqrt(sum 12 / sum A_g)` over the young x blowout cell. Disclosed: this reads the same minutes
  data in the already-measured, no-exclusion form; it is the cheapest honest spread estimate and does not show the
  excluded ratio.
Any failure: not frozen; recorded, not chased.

## Priors (stated before the gate script ran; drafted PREDICTIONS_LEDGER row in the report)
- P(G1-G3 pass) 0.95. P(G4 passes) 0.85 (young close games are the thin cell: about 1,000 per season).
  P(G5 passes) 0.60.
- If frozen and run: P(P1 holds both seasons) 0.85; P(P2 holds both seasons) 0.12 (the non-garbage part of a blowout's
  fourth quarter is mostly its first minutes, when benches play in every game type, so young players likely still
  over-index there); **P(PASS) 0.10**. Expected young gap per season: -0.15 (80% interval -0.28 to -0.03).
  Expected veteran gap: +0.20 (80% +0.08 to +0.33).

## Gate result (label-free with respect to the premise; run 2026-10-27 18:48 CDT; STOPPED AT THE GATE on G5)
Script `nba/eval/f12b_closing_risk.py gate` (this branch); outputs `reports/prereg_f12b/gate.json` (sha256
`01cb5f1ff9bd0467`, identical bytes on three runs after rounding to 10 significant digits; parallel float sums differ in
the last bit otherwise), `gate_audit_2022.json` (`22b54cb9a0ccc080`), `gate_audit_history.json` (`48498ade438d80b7`).
About 1 s wall, 0.24 GB peak. `nba.duckdb` data_version `49a2fc3d3dff`; history DB for the audit; `season <= 2024`;
read-only. No ratio with garbage time removed was computed.

| check | 2023 | 2024 | pass |
|---|---|---|---|
| G1a stints cover games | 100% (1,318) | 100% (1,320) | yes |
| G1b stints vs box within 1 min | 99.97% (28,201) | 99.98% (28,240) | yes |
| G2 as written (first Q4 possession >= 700 s) | 79.9% | 79.0% | **no (mis-set bar, A1)** |
| G2-A1 (state from 720 s; first Q4 possession >= 680 s; score_diff non-null) | 100% | 100% | yes |
| G3 last-possession margin within 3 of final | 100% | 100% | yes |
| G4 smallest cell, player-games / games (young x close) | 891 / 273 | 1,108 / 323 | yes |
| G4 young x blowout non-garbage Q4 minutes | 6,848 | 8,023 | yes |
| **G5 projected young-gap 95% half-width** (no-exclusion half-width x inflation) | **0.063** (0.0398 x 1.58) | **0.060** (0.0384 x 1.56) | **no** |

Cells after dropping `A_g < 1` (2023 / 2024 player-games): young close 891 / 1,108, young blowout 1,428 / 1,635, vet
close 3,190 / 3,323, vet blowout 3,678 / 3,581. Garbage time per game: 4.18 / 4.18 min on average, 8.29 / 8.26 min in
blowouts; 984 / 982 games have some; 102 / 114 blowouts are garbage for more than 11 of the 12 Q4 minutes (dropped).

**Amendment A1 (disclosed; adversary to check).** G2 failed as written because possessions begin at their first event
(first Q4 possession start: p1 692 s, p10 697 s, median 704 s, min 683 s; `score_diff` is never null). The bar meant
"the state covers the quarter"; with the first possession's state carried from 720 s it does, by construction. A1
changes the definition only for the first 16-37 s of each fourth quarter, it was decided before any premise number,
and it does not touch who is counted. Same pattern as T515's two amended bars.

**Verdict: the gate FAILS on G5 in both seasons.** At about +/-0.06 per season, a point estimate inside the +/-0.04 band
would not distinguish "the young play the same share" from a gap of -0.10. A FAIL far outside the band would be
readable, but a PASS would not, and a test that can only fail is not a test of the premise. Not frozen.

Data-capture audit (non-negotiable 8). Every season held, same gate code, projected young-gap half-width per season:
2013 0.067, 2014 0.074, 2015 0.069, 2016 0.062, 2017 0.063, 2018 0.059, 2019 0.064, 2020 0.064, 2021 0.060, 2022 0.067
(all fail per season; G2 as written fails in every season too, by the same mechanism). Pooled, measured directly
(bootstrap over the pooled games): 2023+2024 0.044 (fails), 2022-24 0.037 (passes), 2019-21 0.036 (passes), 2013-21
0.022 (passes). No other lineup source exists (stints are derived from our play-by-play); `draft_year` is the only
static input, and history `players_static` was filled after 2026-10-12 (audit D12), so history cells are a replication,
not a reference. **Repairable gaps: none. The per-season design fails on data after the audit; a pooled design would
pass.**

## Options for Devin (none frozen)
- **A: close F12 now.** Record "premise untestable per season at +/-0.04 once garbage time is removed; without the
  exclusion it fails in 12 of 12 seasons". Cost zero.
- **B (modeler's recommendation if the question is still wanted): F12B_POOLED, a new id.** Select = history 2013-2021
  pooled (11,516 games; projected half-width 0.022), report = 2022-2024 pooled (3,958 games; 0.037); same definition,
  A1 included, same P1 / P2 bands on the pooled gaps, both must hold. Season 2025 untouched. Cost: seconds of CPU and
  one more adversary read. It answers the audit's question on valid stints with a definition fixed in advance. Expect a
  FAIL (P(PASS) about 0.10), and expect a PASS to lead to an F12B_MODEL that probably stops at its own pts power gate.
- **C: per-season with a wider band (+/-0.08).** Not recommended: it widens the band to fit the noise after seeing it.

## What would reverse this
More scored seasons at 2023-24 quality (only by pooling, option B), or a precision estimate on the excluded ratio that
comes out much tighter than the projection (would need the premise data itself; not allowed before freeze).

## Compute estimate
Mac CPU, single process: the premise stage is about the gate's cost (seconds, under 0.5 GB); B is about 10 s across
both DBs. No heavy lock needed; nothing needs to wait for tonight's replay oracles.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: descriptive gate; the host a later rule would touch is the gradient-boosted short-minutes classifier.
1. **Information or capacity?** Information: whether pre-tip closeness tells us who plays the fourth quarter. The
   pre-tip proxy (`abs_margin`, `min10`, `starter10`) already sits in the trees, so a later model's new information is
   only the player's closer habit.
2. **Enough data?** For the descriptive premise: not per season (G5); yes pooled (audit). For a later tree feature:
   about 25,000 rows per season; the pts floor is the binding constraint (T506 / T507), not the rows.
3. **Test valid?** T185 invalid (pre-v2 stints). This draft: stints valid (99.97%), definition fixed before any ratio,
   but per-season precision fails, so a run would be invalid as a PASS detector (asymmetric). Garbage time is defined
   from score and clock only, not from who is on the floor.
4. **Lineage and inherited guards:** F12 thresholds (close <= 5, blowout >= 15, idx 2 / 5, +/-4%, >= +4%) carried
   unchanged; F12's one allowed follow-up is spent by this id (or by B, whichever is frozen, not both).
5. **Scored against:** nothing; no arm, no market.

## Prior scoring (gate priors, stated above before the script ran)
G1-G3 pass: P 0.95; G3 passed, G2 failed as written (miss, a bar error, not data). G4 pass: P 0.85, passed. G5 pass:
P 0.60, failed (miss: I underrated how few young close-game player-games a season holds, about 900-1,100).

=== RESULTS BELOW ===

(not run)
