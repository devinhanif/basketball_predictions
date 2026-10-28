# F12B_POOLED: the closing-lineup premise on rebuilt stints, garbage time excluded, pooled select and report windows (pre-registration DRAFT, descriptive)

Status: **DRAFT 2026-10-27 19:45 CDT (from `date`), NOT frozen.** Modeler, branch `agent/modeler-f12b`. Everything above
`=== RESULTS BELOW ===` is the rule. Freeze evidence would be the commit `prereg(F12B_POOLED): <sha256>` plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/F12B_POOLED.md | shasum -a 256`. No premise ratio with the
garbage-time exclusion has been computed for any season, no gap point estimate has been written, no `cl_*` column
built, no arm fitted, season 2025 never loaded, every connection `read_only=True`.

- **Advice or momentum:** *follows advice*: option B of docs/prereg/F12B_CLOSING_RISK.md ("modeler's recommendation if
  the question is still wanted"), which stopped at its own gate on G5 (per-season precision 0.060-0.063 > 0.04). The
  main session asked for this draft (2026-10-27, redo wave 1). Devin has not yet said the question is still wanted; the
  safe default if he does not is option A (close F12 now, cost zero). This draft exists so the choice is between two
  finished things.
- **This is F12's one allowed follow-up.** F12's kill criteria allow at most ONE pre-registered follow-up, then closed.
  F12B_CLOSING_RISK was never frozen (stopped at the gate), so the follow-up is still unspent; if F12B_POOLED is frozen,
  it spends it. Whatever this returns, F12 is then closed: no further cut-off, window, definition or season variants.
- **Model class:** descriptive gate (no model). The thing a later rule would fit is a feature group for the
  short-minutes classifier of the lower-tail mixture (gradient-boosted trees).
- **Re-tests:** T185 (INVALID: pre-v2 stints). Validity audit 2026-10-12, redo rank 8.
- **Lineage:** docs/prereg/F12_CLOSING_RISK.md (frozen sha256 484e35b7, T185); docs/prereg/F12_RETEST.md (2026-10-14:
  1b passes at 99.97%, premise 2b fails without any exclusion, -32.4% pooled 2022-24 and in all 12 seasons 2013-24);
  docs/prereg/F12B_CLOSING_RISK.md (draft 624b0d18, gate STOPPED on G5; its definition, tiers and amendment A1 are
  carried here verbatim).

## What is already known (data exposure, honest)
Seen: F12_RETEST's no-exclusion ratios and per-season gaps (young about -0.33, veterans about +0.36 to +0.40);
F12B_CLOSING_RISK's gate (cell counts, garbage minutes per game, per-season projected half-widths 0.059-0.074 for
2013-2024) and its audit's pooled half-widths (2013-21 0.022, 2022-24 0.037). The gate below re-measures those two
pooled numbers with this rule's code (same values). Not seen: any ratio or gap with garbage time removed, in any
season or window; the per-game mean-of-ratios version.

## Hypothesis (Devin's premise, FAN_KNOWLEDGE round 4)
Outside garbage time, coaches give veterans more of the fourth quarter in close games than in blowouts, and young
players about the same share in both. H0 for the young: the gap is outside +/-0.04.

## Definitions (F12B_CLOSING_RISK's, unchanged)
1. **Garbage time** (game state only, fixed before any ratio): a fourth-quarter possession is garbage iff its START
   margin `m = |score_diff - pts|` (offence perspective, table `possessions`) meets `m >= 25` with clock > 540 s left,
   `m >= 20` with 360 < clock <= 540, or `m >= 10` with clock <= 360. The garbage state holds from that possession's
   `clock_start` until the next possession's `clock_start` (the last one until 0:00). **Amendment A1 (carried):** the
   FIRST Q4 possession's state holds from 720 s (possessions start at their first event, median 704 s; its start margin
   is the end-of-Q3 margin). These are the clock / margin tiers of the public Cleaning the Glass definition; its "two or
   fewer starters on the floor" condition is NOT used (it selects on who is on the floor, the quantity measured).
   Overtime excluded, as in F12.
2. **Normalisation.** For player-game i in game g: `q4ng_i` = his Q4 floor minutes outside garbage (from `stints`),
   `A_g` = 12 minus game g's Q4 garbage minutes. Cell ratio `R = (sum q4ng_i / sum A_g) / (sum q13_i / (36 N))` over the
   N player-games of the cell (baseline 1.0). Player-games with `A_g < 1` minute are dropped (Q4 share undefined).
   Gap = `R_close / R_blow - 1`.
3. **F12's cells (unchanged):** close = final |margin| <= 5, blowout >= 15; veteran = `season - draft_year + 1 >= 5`,
   young `<= 2`; drafted players only; every game in `games` for the season (regular season and playoffs); stints from
   `stints`, box rows from `player_game_stats` (a stint player-game counts only with a box row).

## The one change from F12B_CLOSING_RISK: two pooled windows instead of two seasons
| window | seasons | source | games | role |
|---|---|---|---|---|
| **SELECT** | 2013-2021 (9 seasons) | `data/history/nba_history.duckdb` | 11,516 | replication: computed FIRST |
| **REPORT** | 2022-2024 (3 seasons) | `nba.duckdb` (`season <= 2024`) | 3,958 | the reading for today's league |
Each window is pooled: the cell ratios sum over every player-game of every season in the window; the bootstrap
resamples games of the whole window. Seasons are weighted by their player-games (no re-weighting; see threats).
Season 2025 is the holdout and is never read.

## Inference
Game-clustered bootstrap of each gap (B 2,000, seed 0; resample the window's games, recompute both cell ratios), 95%
percentile CI, per window. The decision is on the point estimate, as F12's check 2 and F12B's rule were, plus the
precision guard below. No multiplicity correction: a stopping premise, not a claim; nothing is promoted on it. n
(player-games and games per cell) is reported beside every number.

## Decision rule (fixed now)
Order: compute SELECT first. If SELECT fails P1 or P2, the verdict is FAIL and the REPORT window's gaps are **not
computed** (2022-24 stays unexposed to this premise). Otherwise compute REPORT.
- P1 (veterans): gap >= +0.04 in SELECT AND in REPORT.
- P2 (young): |gap| <= 0.04 in SELECT AND in REPORT.
- Precision guard (new, protects the asymmetric-test problem that stopped F12B): the REPORT young gap's realised 95%
  half-width must be <= 0.05 (the gate projects 0.037). If it is wider, the verdict is INCONCLUSIVE, which closes F12
  exactly as a FAIL does.
- **PASS** iff P1 and P2 hold in both windows and the precision guard holds. PASS earns only a NEW pre-registration
  (F12B_MODEL: the `cl_*` feature group for the short-minutes classifier) with its own minimum-data and power gate;
  nothing is fitted under this id. Warning stated now: F12's floors (pts integer CRPS -0.003, minutes CRPS -0.03 min)
  are probably below a whole-population pts test's resolution (T506: half-width 0.0055-0.0074 per season), so
  F12B_MODEL may stop at its own power gate; minutes would then have to be its primary target.
- **FAIL** or INCONCLUSIVE: F12 closed permanently ("premise not replicated on valid stints, garbage time removed,
  pooled windows").
- Descriptive only, cannot rescue a FAIL: per-season gaps inside each window (per-number intervals, labelled as
  unpowered: 0.06-0.07 half-widths), the per-game mean-of-ratios version (`cl_cs_*` definition), the no-exclusion ratios
  for comparison, young starters, the idx 3-4 group.

## Kill criteria
- A gate check fails on the run's data: stop, report, fit nothing; not chased.
- Any premise number (a gap with garbage time removed) computed before the freeze commit: the rule is void.
- After the verdict: no window, tier, band, cell or definition variant under any id. F12 is closed either way unless
  PASS, and PASS only opens F12B_MODEL.

## Minimum-data gate (NN8): measured 2026-10-27 19:40 CDT (from `date`), counts and precision only. ALL PASS
Script `nba/eval/f12b_pooled.py gate` (this branch; it calls `nba/eval/f12b_closing_risk.py`'s `run_gate`, `load`,
`cells`, `boot_gap` unchanged); output `reports/prereg_f12b_pooled/gate.json` (text sha256 `f4e6e979e49fd15b`);
2.0 s wall, 0.48 GB peak, single process. Data: `nba.duckdb` read-only (manifest `docs/data_manifests/latest.json`
data_version **49a2fc3d3dff**, committed at 9aa5de5; file mtime 2026-10-27 19:20 CDT), `data/history/nba_history.duckdb`
read-only (mtime 2026-10-13 13:16 CDT). `season <= 2024` in every query; max season read 2024 (REPORT) and 2021
(SELECT). No ratio with garbage time removed was computed; G5 reads only the spread of the already-measured
no-exclusion young gap.
Checks (bars written in F12B_CLOSING_RISK before its gate ran; this rule's G5 bar is the same 0.04, applied to the
pooled windows):
- G1a stints cover >= 95% of games; G1b stint minutes within 1.0 min of box on >= 95% of player-games; G2-A1 first Q4
  possession starts at >= 680 s with `score_diff` non-null on >= 98% of games (state carried from 720 s); G3 last
  possession margin within 3 points of the final |margin| on >= 95% of games. **Per season, every season of both
  windows.**
- G4 per window: each of the four cells >= 500 player-games and >= 150 games after dropping `A_g < 1`; young x blowout
  non-garbage Q4 minutes >= 1,000.
- G5 per window: projected 95% half-width of the pooled young gap <= 0.04 (bootstrap half-width of the no-exclusion
  young gap over the window's games, times `sqrt(12 N / sum A_g)` over the pooled young x blowout cell). The REPORT
  window is the binding one; SELECT is required too.

| check | SELECT 2013-2021 | REPORT 2022-2024 | pass |
|---|---|---|---|
| G1a stints cover games (worst season) | 99.92% (2014) | 100% | yes |
| G1b stints vs box within 1 min (worst season, n player-games) | 98.66% (2020, 24,845) | 99.97% (2023, 28,201) | yes |
| G2-A1 (state from 720 s; first Q4 possession >= 680 s; score_diff non-null) | 100% every season | 100% every season | yes |
| G3 last-possession margin within 3 of final (worst season) | 99.91% (2019) | 100% | yes |
| games / player-games in the window | 11,516 / 242,457 | 3,958 / 84,180 | n/a |
| G4 smallest cell, player-games / games | 10,014 / 2,627 (young close / young blowout) | 3,087 / 926 (young close) | yes |
| G4 young x blowout non-garbage Q4 minutes | 56,671 | 21,663 | yes |
| G5 no-exclusion young-gap half-width x inflation | 0.0139 x 1.560 = **0.0217** | 0.0234 x 1.569 = **0.0367** | yes |
Cells after dropping `A_g < 1` (player-games / games): SELECT young close 10,014 / 2,952, young blowout 11,496 / 2,627,
vet close 32,703 / 3,089, vet blowout 29,837 / 2,662; REPORT young close 3,087 / 926, young blowout 4,444 / 954, vet
close 10,126 / 1,004, vet blowout 10,397 / 965.
- Margin to the bar: the REPORT projection is 0.0033 under 0.04. The projection is an estimate of the excluded ratio's
  spread, hence the realised-precision guard in the decision rule (0.05).
- Data-capture audit (NN8): every season we hold is used (2013-2021 history, 2022-2024 production; 2025 is the holdout).
  No other lineup source exists (stints are derived from our play-by-play). `draft_year` is the only static input; the
  history `players_static` was filled after 2026-10-12 (audit D12), which is why SELECT is a replication, not the report.
  Repairable gaps: none needed; the gate passes.
- Gate prior: none was written before this gate ran, and it would have been uninformative: F12B's audit had already
  measured both pooled half-widths (0.022, 0.037) with the same code. Recorded, not hidden.

## Priors (stated before any premise number; PREDICTIONS_LEDGER row drafted in the report)
- P(SELECT P1 holds) 0.90; P(SELECT P2 holds) 0.12; P(REPORT P1 | SELECT passes) 0.90; P(REPORT P2 | SELECT passes) 0.65;
  P(precision guard holds) 0.85. **P(PASS) 0.05** (product 0.054).
- Expected young gap: SELECT -0.14 (80% interval -0.27 to -0.02); REPORT -0.15 (80% -0.28 to -0.03). Expected veteran gap:
  SELECT +0.18 (80% +0.06 to +0.30); REPORT +0.20 (80% +0.08 to +0.33). Reason for the young prior: the non-garbage part of
  a blowout's fourth quarter is mostly its first minutes, when benches play in every game type, so young players
  probably still over-index there even with garbage removed.

## Threats to validity (declared)
- Era pooling: 2013-21 weights the pre-load-management years equally with 2019-21; coaching habits may have moved. This
  is why SELECT replicates and REPORT decides, and why both must hold.
- 2019-20 bubble and 2020-21 attendance seasons are inside SELECT; not removed (removing them after seeing anything would
  be a variant).
- `draft_year` in history was filled after 2026-10-12; misfiled years move players between idx groups (descriptive check:
  share of drafted player-games with a null `draft_year`, printed beside each window).
- Final margin defines close / blowout, so a game's fourth quarter partly causes its own cell (F12's construction,
  carried). Garbage time removes the most mechanical part of that.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Class: descriptive gate; the host a later rule would touch is the gradient-boosted short-minutes classifier.
1. **Information or capacity?** Information: whether pre-tip closeness tells us who plays the fourth quarter. The
   trees already see `abs_margin`, `min10`, `starter10`; a later model's new information is only the closer habit.
2. **Enough data for this class?** For the premise: yes pooled (G5 0.022 / 0.037), not per season (F12B's G5). For a
   later tree feature: about 25,000 rows per season; the binding constraint is the pts floor's resolution, not rows.
3. **Test valid?** Stints valid (G1b >= 98.7% every season); definition fixed before any ratio; per-window precision
   adequate for a PASS to be readable (G5 plus the realised guard); garbage defined from score and clock only.
4. **Lineage and inherited guards:** F12 thresholds (close <= 5, blowout >= 15, idx <= 2 / >= 5, +/-0.04, >= +0.04) and
   F12B's definitions and A1 carried unchanged; F12's one follow-up is spent by this id if frozen.
5. **Scored against:** nothing; no arm, no line, no market.

## What each choice protects against
- Garbage-time exclusion: bench players finishing decided games standing in for coaching behaviour.
- Pooled windows: a test that can only fail (F12B's per-season precision 0.06 against a 0.04 band).
- SELECT first, REPORT not computed if SELECT fails: exposing 2022-24 to a premise that did not replicate.
- Both windows must hold: a pooled result carried by one era.
- Realised-precision guard: a lucky point estimate inside the band read as "the same share".
- Point-estimate decision with F12's bands: no band chosen after seeing spreads (option C of F12B was rejected for that).

## Outputs, harness and the command (premise harness written after the freeze; adversary reads it before the run)
`nba/eval/f12b_pooled.py` gains a `premise --sha <64 hex>` stage after the freeze (refuses unless the doc hashes to
`--sha` and the freeze commit exists; SELECT first, REPORT only if SELECT passes). Outputs
`reports/prereg_f12b_pooled/{gate,results}.json`. Command (maintainer):
`.venv/bin/python -W ignore -m nba.eval.f12b_pooled premise --sha <64 hex> --out reports/prereg_f12b_pooled/results.json`
Runtime: seconds (the gate took 2.0 s, 0.48 GB). Read-only DuckDB; it does not need `data/ops/heavy.lock`, and it does
not conflict with the 21:05 CDT replay oracles (no writer, no lock); running it outside 21:05-21:45 is still preferred.

## Open choices (defaults taken)
1. Precision guard 0.05 (realised REPORT young half-width). Alternative: none (F12B's design), or 0.04 (the projection's
   bar; would make INCONCLUSIVE likely, a guess of about P 0.35).
2. REPORT not computed if SELECT fails. Alternative: compute both regardless (more descriptive information, but exposes
   2022-24 to a failed premise).
3. Seasons weighted by player-games inside a window. Alternative: equal season weights (a mean of per-season ratios);
   not chosen because single-season cells are thin (the reason this rule exists).
4. Option A (close F12 without running anything) remains available and is the default if Devin does not want the
   question.

=== RESULTS BELOW ===

(not run)
