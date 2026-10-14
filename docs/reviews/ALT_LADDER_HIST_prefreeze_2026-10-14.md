# ALT_LADDER_HIST pre-freeze review (adversary), 2026-10-14

Subject: `docs/prereg/ALT_LADDER_HIST.md` (DRAFT, text sha256 b1e365e4..., main at 200c751), gate
`research/eval/alt_ladder_hist_gate.py`, `reports/prereg_alt_ladder_hist/gate.json` (sha256 6ee534cc...),
harnesses `research/eval/alt_line_70_eval.py`, `alt_parlay_x_eval.py`, `alt_harness_common.py`.
Clock: `date` read Wed Oct 14 14:51 CDT 2026. No outcome (`y`) was read by this review; season 2025 was
not loaded; every DuckDB was opened `read_only=True`. Attack scripts: scratchpad only (`latency.py`,
`qage.py`, `dups.py`, `tips_check.py`, `alh_ev.py`, `alh_swap.py`, `alh_mkt.py`, `alh_mkt2.py`).

Labels (incident log): **SUBSTANTIVE** = changes a result, a gate or what runs; **COSMETIC** = wording only.
Every fix comes back to the adversary for confirmation (the "cosmetic applied without a round"
convention was not adopted).

## Top finding (B1, SUBSTANTIVE, blocker): DraftKings December 2023 "points" alternates are not points ladders

DraftKings `player_points_alternate` at the `t60` snapshots of 2023-12-07 to 2023-12-19 carries ladders
that do not belong to the points market. Example, game 0022301229 (2023-12-07, MIL v IND), player
201572: raw vendor file `data/odds/raw/the_odds_api/2023/0022301229/odds_t60_eu-us_a3df606d.json.gz`
lists DraftKings points alternates O5.5 -175 / U5.5 +125, O8.5 +330 / U8.5 -500; DraftKings' own main
markets at the same snapshot are points 12.5 (O -120) and rebounds 6.5 (O +130); FanDuel's points
alternate is O8.5 -490. The ladder matches his rebounds. The vendor file has the error, so the parser is
not the cause.

Detection (prices only): a (book, game, player, stat) ladder is "off" when its rung with raw implied over
probability nearest 0.5 (within 0.15 of 0.5) is more than 3.0 points from the same book's main over line
at the same snapshot (`alh_mkt.py`, `alh_mkt2.py`, exit 0):

| Book | 2023 off / ladders | 2024 off / ladders |
|---|---|---|
| draftkings | 359 / 25,342 (pts, 12 snapshot dates 2023-12-07..12-19, plus 1 on 2024-02-09) | 4 / 53,667 |
| fanduel | 0 / 32,149 | 2 / 44,614 |
| williamhill_us | 419 / 22,385 | 635 / 42,931 |
| betmgm | 0 / 753 | 417 / 39,259 |
| bovada | n/a | 326 / 20,300 |
| betrivers | n/a | 101 / 4,702 |
| others | <= 1 | 0 |

DraftKings pts in December 2023: 26.7% of 1,343 ladders are off; no other DraftKings stat-month passes 2%.

Effect on the primary set (DraftKings, report-window and lead-cap exclusions applied, P >= 0.70), from
model P and price only:
- 2023: 1,032 of 27,958 rungs fall in 351 off ladders (83 games), median decimal 2.1 (all DraftKings
  P >= 0.70 rungs: 1.20). 3.3% of DraftKings 2023 P >= 0.70 rungs are priced at decimal >= 2.0, against
  0.03-0.05% in the other three (book, season) cells.
- Those rungs carry **70% of DraftKings 2023's model-claimed EV**: +7.0% with them, +2.2% without. Pooled
  claimed EV goes from about +0.8% to about -1.0%. Bin 85 (2023) goes from +4.5% to +2.4%.
- If those rungs hit near the model's P, they add about +1,373 stake units: **+0.018 to pooled E1 ROI and
  +0.049 to DraftKings 2023**. That is about the whole +0.02 floor. The 2024 season-sign condition is the
  only thing standing between this artifact and a PROFITABLE word.
- Arm B (best price across books) and C1 are hit the same way: best-price selection seeks out exactly
  these mislabelled long prices (williamhill_us, betmgm, bovada, betrivers also have off ladders).

Part 1 uses no prices, but its population is "rungs DraftKings posted for points". These rungs are
another stat's ladder labelled points, so they do not belong in it either (most sit at P >= 0.90,
outside the headline band).

**Fix (SUBSTANTIVE; insert after rule line 14 as a new bullet; then implement it in the gate, re-run,
and replace the gate tables and the claimed-EV sentences on lines 83 and 97):**
> - **Ladder integrity (price-only exclusion, fixed before any outcome).** A (book, game, player, stat)
> ladder is excluded from every part, arm, comparator, replicate and arm B, and counted, when its rung
> whose raw implied over probability `1/d` is nearest 0.5 (and within 0.15 of it) lies more than 3.0
> points from the same book's main-market over line for that player and stat at the same `t60` snapshot.
> A ladder with no such rung, or with no main line, is kept and counted. Why: DraftKings
> `player_points_alternate` at the snapshots of 2023-12-07 to 2023-12-19 carries ladders that are not
> points ladders. Example: game 0022301229, player 201572, alternate O5.5 -175 / O8.5 +330, against
> DraftKings' own main points 12.5 and main rebounds 6.5 +130, and FanDuel's points O8.5 -490. Measured
> on prices only, no `y`: DraftKings 359 of 25,342 ladders (2023) and 4 of 53,667 (2024); FanDuel 0 and
> 2; williamhill_us 419 and 635; betmgm 0 and 417; bovada 326 (2024); betrivers 101 (2024). In the
> DraftKings primary set this removes 1,032 of 27,958 P >= 0.70 rungs in 2023 (351 ladders, 83 games),
> which carry 70% of DraftKings 2023's model-claimed EV (+7.0% with them, +2.2% without). Sensitivity S3
> (descriptive, never decides): the excluded ladders added back.

Per DECISIONS 2026-10-14 14:40, a substantive change goes back to Devin. The modeler should also
restate the Part 2 prior (PREDICTIONS_LEDGER line 25), because its stated reason ("claimed EV about
+0.8% pooled") was computed with the corrupted ladders. Keep the original numbers beside the restated
ones; no outcome exists yet, so this is not a post-hoc change. Also recommend a DATA_CHANGELOG
data-quality row for the vendor error.

## Answers to the brief

**(1) Timing: does the report-window exclusion close the stale-price channel? Yes for the model's
information. Measured, not assumed.**
- The model's only time-gated input is `player_availability` (source `nba_official_report`). It is
  gated `as_of <= tip_et - 60` on the scheduled tip (`nba/features/injury_report.py:59-83`; ContextResidualConfig
  default `tip_source="real"` at a2e7609, the sha that built the model rows, `model_rows_*.meta.json`).
  Lineup features are off (`build_features(..., lineups_known=False)`). The window uses
  `data/ops/model_clv_pairs` tips; these equal `build_game_tipoff()` for all 1,318 and 1,315 games
  (0 differences, 0 missing, so no 19:00 proxy fallback; `tips_check.py`, exit 0). The window reads
  stamps from every source and player, a superset of what the model reads, so it errs conservative.
- The stored stamps are only 11:00 and 17:00 ET (6,673 game-stamps). The window games are 37 + 2 (2023)
  and 59 + 3 (2024) games with scheduled tips at 18:00 or 12:00.
- Publication latency, which the draft lists as unverified, is now bounded. In retained DraftKings games
  the latest usable stamp precedes the snapshot by at least 55.6 min in 99% of games (p01 = p05 = 55.6,
  median 115.6). Only 2 games per season have a stamp 15-30 min before the snapshot, and none within
  15 min (`latency.py`, exit 0). A late-published report could leak only with a latency above 25 min (4
  games) or above 55 min (all other games).
- Quote timestamps: `last_update` is at most 1.38 min after `snapshot_ts`, and 0 of 1,157,478
  DraftKings/FanDuel over quotes have `last_update > tip - 60` (`qage.py`, exit 0).
- News outside the report (beat reporters, lineups) is not a model input, so it cannot create model-side
  lookahead. It is the T-64 bettor's real exposure. The own-player DNP void is a real sportsbook option,
  but it may not exist on Kalshi or Polymarket (a forward-rule point, N8).
- Verdict on (1): closed. The DECISIONS 14:40 reversal condition ("exclusion does not close the timing
  channel") is not triggered.

**(2) DraftKings chosen after counts.** No outcome was read, so this is not outcome selection. But the
gate json and line 83 showed model-claimed EV per book when the book was chosen, and DraftKings had the
highest (+7.0% in 2023). The disclosure on line 67 names only counts (N2). After B1 the DraftKings
claimed-EV advantage mostly disappears. "Last price before the snapshot": the duplicate FanDuel
listings sit in the same market object with the same `last_update` (1,611 of 1,611 duplicate keys in 22
sampled 2024 files, `dups.py`). So no time order exists, and "last" means the last listing in the vendor
response (N3). DraftKings duplicates have identical prices, so the primary arm is not affected.

**(3) Row floor 3,000 and pooled Part 1S.** The floor does not bind; the width does (fg3m 2023, 0.0297 <=
0.03). Line 29 omits that ast 2023 (4,784) is also below the old 5,000 floor (N4). Part 1S pooled is
defensible because 21 of 32 season cells fail the width gate. However, CALIBRATED needs no per-season
condition while OVERCONFIDENT/UNDERCONFIDENT need the same sign in both seasons, so the asymmetry
favours CALIBRATED (N5, disclosure).

**(4) Mirror of ALT_LINE_70: correct.** Part 1 words and margins (+-0.02 point, +-0.03 interval), BH over
the 8 headline cells, union interval, p = max(p_game, p_date), Part 2 floor +0.02 with lo > 0 and both
seasons > 0, NOT PROFITABLE iff hi < +0.02, Part 2S BH over 4 with one-sided p: all match
ALT_LINE_70 lines 32-39, 73-75 and 90. Departures are listed in lines 24-34. Which cells can issue which
word:
- Headline: all 8 cells can issue any word. The 2023 cells can be CALIBRATED only if the empirical
  interval is narrower than the conservative bound.
- Part 1S: words only on the 16 pooled cells; no season-cell words.
- Part 2: one pooled word; by-stat words descriptive unless BH q < 0.05 over 4.
- Part 2S: 4 pooled bin words, bin 85 included.
- FanDuel, Fanatics, arm B, C1/C2: descriptive.

**(5) Harness pins: correct, with two holes.**
- Recomputed `awk` hashes: ALT_LINE_70 a72bdff245ac5df6 = pin = commit 016481a; ALT_PARLAY_X
  91d345950abf03ae = pin = commit ec2c987. ALT_LADDER_HIST b1e365e4 has no `prereg(ALT_LADDER_HIST)`
  commit.
- Dry runs with `--check-only --pre-alt-backup` both exit 2. The only failing checks are the two
  ALT_LADDER_HIST checks (no prereg commit; status not FROZEN). The other 53 of 55 (ALT_LINE_70) and 69 of 71 (ALT_PARLAY_X) checks pass:
  - construction files: alt_parlay_x_gate 169b8194491a and alt_line_70_gate 8da317480035, both matching
    the frozen ALT_PARLAY_X line 131;
  - numpy 2.5.3;
  - odds fingerprint 5,223,918 rows, hash_sum equal to the pre-alt backup;
  - every ALT_LINE_70 gate count.
- Hole H1: `check_rule(..., expected_sha=None)` (`alt_harness_common.py:117-139`, called at
  `alt_line_70_eval.py:873` and `alt_parlay_x_eval.py:462`) accepts any ALT_LADDER_HIST sha that some
  prereg commit carries. A re-freeze after ALT_LINE_70's results would pass, which is the ordering the
  check exists to enforce.
- Hole H2: without `--pre-alt-backup`, the odds-store check is recorded as ok=True with "NOT VERIFIED"
  (`alt_line_70_eval.py:899`, `alt_parlay_x_eval.py:487`).

**(6) Pulled-line guard.** Attack (viii) correctly does not apply: there is one capture, the bet set is
what was posted at T-64, and there is no survivorship between captures. The integrity failure that
actually exists at a single capture is B1. Drift is proxied on main markets (line 21), which is fine
as a descriptive check.

**(7) Prior row present.** PREDICTIONS_LEDGER line 25, dated before any outcome. It points to "branch
agent/modeler-alt-ladder", but the draft is now on main (cosmetic). Its rationale needs restating
after B1 (above).

## Per-check table

| # | Check | Command (scratchpad unless noted) | n | Result | Pass |
|---|---|---|---|---|---|
| R | Gate reproduces byte-identically | `.venv/bin/python -W ignore -m research.eval.alt_ladder_hist_gate --data-root ... --nba-db ... --out <scratch>` | full gate | sha256 6ee534cc... both | PASS (exit 0) |
| 2 | Planted future / timing: stamps in (snap, tip-60] and near-snapshot latency | `latency.py` | 1,012 / 1,314 DK games | 39 / 62 window; 0 stamps within 15 min before snap; 2 / 2 within 30 | PASS (exit 0) |
| 2 | Quote after cutoff | `qage.py` | 1,157,478 DK+FD over quotes | 0 `last_update > tip-60`; max +1.38 min after snap | PASS (exit 0) |
| 2 | Tip source agreement, model vs window | `tips_check.py` | 1,318 / 1,315 games | 0 differences, 0 proxy fallbacks | PASS (exit 0) |
| 3 | Missingness: who is in the model rows | code read: `build_features` minutes > 0, `stat_frame` n_prior >= MIN_PRIOR | all rows | played-only (any minutes), matching sportsbook action; no short-minute filter (the ridge pattern is absent) | PASS (by reading; not re-run) |
| I | Price integrity of posted ladders | `alh_swap.py`, `alh_mkt.py`, `alh_mkt2.py` | 25,342 / 53,667 DK ladders | 359 off in 2023 (pts, Dec 7-19); 70% of DK 2023 claimed EV | **FAIL (B1)** |
| D | Duplicate listings are time-ordered? | `dups.py` | 1,611 FD duplicate keys, 22 files | same market object, same `last_update`: no time order | wording (N3) |
| 5 | Harness dry runs refuse until frozen | `alt_line_70_eval --check-only --pre-alt-backup ...`; `alt_parlay_x_eval` the same | 55 / 71 checks | exit 2; only the two ALT_LADDER_HIST checks fail | PASS |
| 6 | Integer support | rule line 37; gate half-point filter (`alt_ladder_hist_gate.py:161`) | all rungs | P = P(Y >= point + 0.5), half-points only, no pushes | PASS |
| 1, 4 | Shuffled labels; knockout | n/a pre-freeze (nothing fitted; no outcome) | | | n/a |

## Findings (exact line, exact fix)

- **B1 SUBSTANTIVE, blocker.** Rule lines 12-14 have no ladder-integrity guard. Fix: the bullet above.
  Then add the exclusion to `alt_ladder_hist_gate.py` `alt_over_rungs` (after the half-point filter, line
  162), re-run the gate, and replace the tables on lines 61-90 and the claimed-EV text on lines 83 and
  97.
- **N1 COSMETIC.** Line 20, replace "the publication latency of a report stamped 17:00 is not measured
  here (unverified; ... would not be flagged)." with: "publication latency is bounded by the measured
  gaps: in the retained games the latest usable stamp precedes the snapshot by >= 55.6 min in 99% of
  games (median 115.6); 2 games per season have a stamp 15-30 min before the snapshot and none within
  15 min, so a late-published report could enter the model but not the price only if its latency
  exceeded 25 min (4 games) or 55 min (all others). No quote has `last_update` after tip - 60 (max 1.38
  min after the snapshot)."
- **N2 COSMETIC.** Line 67, after "was made after the counts;" insert: "the per-book model-claimed EV
  (line 83; DraftKings highest, +7.0% in 2023) was visible when the book was chosen, though not used
  as a reason;".
- **N3 COSMETIC.** Line 14, after "(Devin's default: last price before the snapshot)" insert: "(the two
  listings of a duplicated key share one market object and one `last_update`, so no time order exists;
  'last' is the last listing in the vendor response)". Line 129 "a lookahead-free price rule": keep it,
  because both listings are at the snapshot.
- **N4 COSMETIC.** Line 29, replace "DraftKings fg3m 2023 has 3,209 band rungs" with "DraftKings fg3m
  2023 has 3,209 and ast 2023 4,784 band rungs (both below 5,000; fg3m below 4,000)".
- **N5 COSMETIC.** Line 53, append: "Asymmetry, disclosed: CALIBRATED is decided on the pooled cell
  alone, while OVERCONFIDENT / UNDERCONFIDENT also require the same sign in both seasons, so offsetting
  season gaps can pool to CALIBRATED; the per-season gaps are printed beside every word."
- **N6 SUBSTANTIVE (small).** Lines 49 and 104 do not say what a failed attack does. Append to line 49:
  "A failed attack is reported as 'PROFITABLE, not believed (attack <x> failed)'; arm A is then not a
  forward-shadow candidate under this id." (ALT_LINE_70 is frozen with the same gap; it cannot change
  there.)
- **N7 SUBSTANTIVE (small).** Line 150, the scoring harness is written after the freeze, so its guards
  must be in the frozen text. Append: "The harness refuses unless: every scored row has `snapshot_ts <=
  tipoff_utc - 60 min` and `last_update <= tipoff_utc - 60 min`; the window and lead-cap game counts and
  the ladder-integrity exclusion counts equal the gate tables; `alt_ladder_hist_gate.py` and
  `alt_line_70_gate.py` hash to the sha256 values recorded at the freeze; exactly one distinct
  `prereg(ALT_LADDER_HIST)` sha exists in history, and it is the one the ALT_LINE_70 result recorded."
- **N8 COSMETIC.** Line 109, item (2), append: "and the venue's DNP settlement (a sportsbook voids; a
  binary contract may settle No), since the T-64 void is part of what this rule measures."
- **H1 (harness, SUBSTANTIVE, before ALT_LINE_70 runs).** `alt_line_70_eval.py:873`,
  `alt_parlay_x_eval.py:462`: once ALT_LADDER_HIST is frozen, pass its sha as `expected_sha`, or fail
  when `git_prereg_shas` returns more than one distinct sha, and write the ALT_LADDER_HIST sha in full
  into results.json.
- **H2 (harness, minor).** `alt_line_70_eval.py:899`, `alt_parlay_x_eval.py:487`: make
  `--pre-alt-backup` required, or pin `{rows: 5223918, hash_sum: 48161699822608609601892543}` in code
  and compare against it.
- **m1 minor.** CI code lives in `research/eval/alt_harness_common.py:151-234`, not
  `truth/bootstrap.py` (which does not exist; `nba/truth/` holds metrics.py and walkforward.py). Flagged
  per role, not blocking.
- **m2 COSMETIC.** PREDICTIONS_LEDGER line 25: "(draft, branch agent/modeler-alt-ladder)" becomes
  "(draft, main 200c751)".

## Family and power

Families as run: Part 1 headline BH over 8; Part 1S BH over 16 (pooled); Part 2 primary is one test (not
in a family); Part 2 by-stat BH over 4 (descriptive); Part 2S BH over 4 (one-sided). The reliability and
distance bins are descriptive, and BH over their own family applies only if one is quoted.
- Power for Part 2 (pre-B1 counts): conservative pooled half-width 0.0196, independent 0.0033.
  PROFITABLE needs a point ROI of about +0.02 or more. INCONCLUSIVE spans roughly 0 to +0.02. After B1
  the 2023 count falls by 1,032 rungs, and the width should move by less than 0.001 (not re-run).
- Part 1 2023 cells are close to the width gate (0.0276-0.0297), and B1 removes few band rungs (most
  off rungs sit at P >= 0.90). The re-run must confirm that pts 2023 still passes.

## Playbook check (docs/MODEL_PLAYBOOK.md)

Class: stored distribution layer (production context-residual quantile forecast; classes "Quantile and
distribution layers" and "Gradient-boosted trees"), tested for calibration and priced; nothing is fitted.
1. **Information or capacity:** neither. No new pre-tip information, no new model. The timing exclusion
   keeps the model's information no newer than the price's (verified above).
2. **Enough data for this class:** yes for calibration at P >= 0.70 (78,246 rungs, 2,224 games). The
   2023 headline cells are near the width bound.
3. **Test validity:** **not yet valid.** The price side carries mislabelled ladders (B1), which could
   produce PROFITABLE without any skill. The timing and played-only conventions are valid.
4. **Lineage:** ALT_LINE_70 design (mirrored correctly), ODDS_HISTORY model rows (real-tip gated,
   played-only), DECISION_SCORING comparators. A new guard is needed: ladder integrity. The guard for
   ALT_LINE_70's two-sided BetRivers main lines never had to face this failure.
5. **Scored against:** hit-rate gap at a threshold, and ROI at the posted price. Not CRPS.

Class check (quantile layers): the reliability table sits at the point that matters, and slices are
fixed in advance. Pass, once B1 removes the off-market rungs from the "posted" population.

## Biggest risk if the claim is wrong

A PROFITABLE (or a 2023-positive Part 2S bin 85) carried by about 1,000 DraftKings December-2023 rungs
that are another stat's ladder priced as points. This is the price-side twin of the T114-T118
scoring-convention artifact.

## Not verified

- Hit rates or ROI of the off rungs (no `y` read). The +0.018 contribution is model-implied.
- Whether the off DraftKings ladders are rebounds in every case (one example confirmed; the
  main-line distance flags them all regardless).
- Whether the williamhill_us / betmgm / bovada / betrivers off ladders share the same cause.
- The model-row universe was checked by reading code, not re-run.
- Main-market drift numbers (line 21) were not recomputed.
- The full test suite: only the 55 alt tests were run.

## Drafted rows (not appended)

- PREDICTIONS_LEDGER, line 25 note (modeler to restate): "Restated pre-freeze after review B1: the
  claimed-EV basis falls from about +0.8% to about -1.0% pooled once the mislabelled DraftKings
  December-2023 ladders are excluded; original numbers kept."
- DATA_CHANGELOG (main session): "2026-10-14 data-quality: vendor `player_points_alternate` for
  draftkings at t60 snapshots 2023-12-07..12-19 carries non-points ladders (359 ladders; detection =
  nearest-0.5 rung > 3 points from the book's main line); also williamhill_us 419 / 635, betmgm 417,
  bovada 326, betrivers 101 ladders; stored rows unchanged; consumers exclude by rule."

First-round verdict (superseded by the re-review below): BLOCKED

## Re-review of revision 2 (main 1d8d86d, branch commit f2f8fe3), 2026-10-14

Rule text sha256 6d292294... (not frozen). Gate json 32a61365... reproduces byte-identically
(`alt_ladder_hist_gate --out <scratch>`, exit 0). No `y` read, no 2025 read, read-only DuckDB.
Scratch scripts: `alh_resid.py`, `alh_resid2.py`, `alh_mono.py`.

### B1: NOT FIXED (SUBSTANTIVE, blocker). The filter I specified misses most of the defect.
The text was applied as written (rule line 15, `alt_ladder_hist_gate.py: off_ladders`). The flaw is in
the rule I proposed, not in how it was applied. Its "within 0.15 of 0.5" condition marks a ladder as
untested and keeps it. Most mislabelled December ladders have no rung near 0.5, because they are a
small stat's ladder priced against a points line. With the filter applied, the DraftKings 2023
primary set still holds:
- **385 P >= 0.70 rungs at decimal >= 2.0 in December 2023** (328 ladders, 81 games, all pts).
  The other DraftKings months have 2-10 such rungs; FanDuel's December has 3.
- 370 of the 385 are "no rung within 0.15 of 0.5". For example game 0022301229, player 1630167:
  nearest-0.5 rung 2.5 against a main points line of 12.5.
- Model-implied profit +1,173 stake units, which is **+0.015 on pooled E1 ROI**, three quarters of the
  floor. DraftKings 2023 claimed EV is still +2.2%, against -3.3% once the top 5% of rungs is dropped.

Fix (SUBSTANTIVE; returns to Devin under DECISIONS 14:40). Rule line 15, after "...at the same `t60`
snapshot", insert:
> "; **or** when any of its rungs at a point at least 1.0 below the main line has raw implied over
> probability more than 0.05 below the main line's raw implied over probability, or any rung at least
> 1.0 above it has one more than 0.05 above (a ladder that contradicts the book's own main line; this
> second test needs no rung near 0.5)"

Then:
- Implement it in `off_ladders` (pass the main line's price as well as its point; `load_main_lines`
  already selects it).
- Re-run the gate.
- Replace the line-15 counts and the gate tables (lines 65-92), plus the claimed-EV text on lines 85
  and 99 and in the ledger restatement (PREDICTIONS_LEDGER line 31).

Measured with this extra test (prices only, `alh_mono.py`, exit 0):
- **Books flagged:** it adds 444 DraftKings 2023 ladders, all in December 2023, and 0 DraftKings 2024
  ladders. FanDuel: 0 in both seasons. williamhill_us +6 / +41, bovada +203, betmgm +1 / +46,
  betonlineag +2 / +31.
- **DraftKings 2023 primary after both tests:** 25,896 P >= 0.70 rungs (1,030 fewer). Claimed EV
  -2.8% (top 5% dropped: -3.9%). 32 rungs at decimal >= 2, of which 4 in December. That is now in line
  with FanDuel 2023 (-4.9%) and DraftKings 2024 (-2.7%).
- **Pooled claimed EV:** about -2.7%.
- **Still kept:** 248 DraftKings 2023 primary rungs have no main line and stay in, counted.

### Main line "nearest 0.5": principled and stated (PASS)
Rule line 15 and `load_main_lines` take the book's own main over line with raw implied probability
nearest 0.5, ties to the lower line. That is the definition of a main line. My first-round script took
an arbitrary first line, which explains betmgm 401 vs 417 and betrivers 0 vs 101. DraftKings and
FanDuel each post one main line, so the primary and replicate counts are unaffected.

### Fix status
| Item | Status | Where |
|---|---|---|
| B1 | **NOT FIXED** (above) | line 15; `alt_ladder_hist_gate.py:142-170` |
| N1 latency bound | FIXED | line 21 |
| N2 claimed EV visible at book choice | FIXED | line 69 |
| N3 duplicate listings have no time order | FIXED | line 14 |
| N4 ast 2023 also below 5,000 | FIXED | line 30 |
| N5 Part 1S asymmetry | FIXED | line 55 |
| N6 failed attack consequence | FIXED | line 51 |
| N7 harness guards in the frozen text | FIXED | line 152 |
| N8 DNP settlement on exchanges | FIXED | line 111 |
| H1 one distinct freeze sha, sha recorded | FIXED | `alt_harness_common.py:140-146`; `--adh-sha`; `res["adh_sha256"]` |
| H2 odds check fails closed | FIXED | `alt_line_70_eval.py:82, 896-905`; `alt_parlay_x_eval.py:482-491` (pinned fingerprint passes without a backup) |
| m2 ledger link | FIXED on line 30, but see C1 | PREDICTIONS_LEDGER |

### New contradictions (revision 2)
- **C1 COSMETIC.** PREDICTIONS_LEDGER lines 25 and 30 are the same ALT_LADDER_HIST prior twice.
  Line 25 has the stale "branch agent/modeler-alt-ladder" link. Merge 1d8d86d kept the old row and
  appended the edited copy, which breaks the rows-drafted-equals-rows-appended check. Fix: delete line
  25; keep 30 and 31.
- **C2 COSMETIC.** Line 32 says "(28,590 rungs" but bin 85 is now 28,470 (line 92). Replace with
  "(28,470 rungs after the ladder filter".
- **C3 COSMETIC.** Line 46 says "DraftKings has 895 (2023)" but the table (line 82) now says 876.
  Replace "895 (2023)" with "876 (2023)".
- **C4 COSMETIC.** Line 114: "New guard: the report-window exclusion (timing section)." Replace with
  "New guards: the report-window exclusion (timing section) and the ladder-integrity filter (data
  section, review B1)." Line 120: after "a documented duplicate-price rule" insert "; the price-only
  ladder-integrity filter (B1)". Table at lines 126-138: add the row "| Ladder-integrity filter, S3 |
  another stat's ladder priced as this stat (a vendor defect) carrying a fake PROFITABLE; S3 shows
  what it removed |".
- Once the B1 fix lands, the numbers on lines 15, 65-92, 99 and 119 all change again. The re-run must
  confirm that the Part 1 pts 2023 cell still passes (6,796 band rungs before the second test).

### Harness dry runs (no `--pre-alt-backup`, so H2 fails closed)
- `alt_line_70_eval --check-only`: exit 2, 57 checks. `alt_parlay_x_eval --check-only`: exit 2, 73
  checks.
- Both fail only the three ALT_LADDER_HIST freeze checks: no prereg commit; 0 distinct shas; status
  not FROZEN.
- The pinned odds fingerprint check passes.

Verification: ruff 0, format 0, mypy 0 (four alt files); pytest 60 passed, exit 0 (`--no-cov`, alt
tests). There is no test of the B1 residual. Add one: a synthetic mislabelled ladder with no rung
within 0.15 of 0.5 must be flagged.

Biggest risk: the residual December-2023 DraftKings rungs (+0.015 model-implied ROI) turning
INCONCLUSIVE into PROFITABLE if 2024 is near zero.

Re-review verdict (superseded by the second re-review below): BLOCKED

## Second re-review of revision 3 (main 01fb38e, branch commit 8e0bc40), 2026-10-14

Scope: B1b and C1-C4 only. No `y` read, no 2025 read, read-only DuckDB. Gate json 005919d4...
reproduces byte-identically (`alt_ladder_hist_gate --out <scratch>`, exit 0). Scratch script:
`alh_resid3.py` (exit 0).

**B1b: FIXED (SUBSTANTIVE; Devin approved).** Rule line 16 excludes the vendor-defect window
outright. Rule line 17 adds the main-line contradiction test as test (b), in my wording; the code is
`alt_ladder_hist_gate.py: off_ladders` (CONTRA_POINTS 1.0, CONTRA_PROB 0.05) and the
`vendor_window` flag in `alt_over_rungs`. Independent re-derivation, prices and model P only:
- **DraftKings 2023 primary set:** 25,826 P >= 0.70 rungs in 971 games. Claimed EV -2.8% (-3.9% with
  the top 5% dropped, so it no longer rests on a few rungs). 28 rungs at decimal >= 2.0, spread over
  the season with 1 in December. FanDuel 2023 has 25 such rungs, 3 in December. December now looks
  like the other months.
- **DraftKings points rungs by UTC snapshot date:** every rung from 12-07 to 12-19 is excluded. On
  12-20 (UTC; mostly the evening games of 12-19 ET) 16 of 593 rungs are flagged by the tests. On
  12-21 and 12-22, 0. The defect ends at the window edge, and the tests catch the overspill.
- **Untested (no main line) primary rungs:** DraftKings 204 (2023) and 1,709 (2024), with 0 and 3 at
  decimal >= 2.0. No sign of a hidden defect.
- **Pooled claimed EV:** about -2.8%. NOT PROFITABLE is the expected word and PROFITABLE needs
  realised ROI well above what the model claims, so the integrity artifact can no longer produce the
  word.
- **Tests:** `test_contradiction_test_flags_a_ladder_with_no_rung_near_even_money` (line 316) and
  `test_vendor_defect_window_is_excluded_outright_for_draftkings_points_only` (line 351) cover the
  residual case I asked for.
- **Disclosure:** the window was picked from prices after the first review, before any outcome, and
  rule line 16 says so ("a stated defect window ..., not a test").
- **Optional COSMETIC, not required:** line 16 "snapshot date" means the UTC date of `snapshot_ts`
  (line 148 defines `snapshot_ts` as naive UTC), so the text is unambiguous. If wanted, append
  "(UTC date of `snapshot_ts`; 12-20 UTC overspill is handled by the tests: 16 rungs)".

**Part 1 pts 2023 width (0.0298).** It passes the 0.03 gate by 0.0002. Every word is still reachable:
- OVERCONFIDENT and UNDERCONFIDENT need only an interval beyond 0 and BH.
- CALIBRATED needs the empirical union interval inside +-0.03. Under the worst-case conservative
  bound that would need |gap| <= 0.0002. Under the independent width (0.0101) any |gap| up to 0.02
  qualifies, and the true width lies between the two.
- Line 82 states this correctly ("conservative hw 0.0276-0.0298 ... CALIBRATED only if the empirical
  decision interval is narrower than the conservative bound"). No change is needed.

**C1-C4: FIXED (COSMETIC).**
- **C1:** PREDICTIONS_LEDGER has one ALT_LADDER_HIST prior row (line 29) plus the restatement (line
  30); the duplicate is gone.
- **C2:** rule line 37 reads 28,299, matching the line 97 table.
- **C3:** line 51 reads 876 (2023).
- **C4:** line 119 has "New guards: ... and the ladder-integrity exclusions"; line 121 adds the
  exclusions to test validity; line 142 adds the protection-table row.

**Harness dry runs** (no backup, so the pinned fingerprint is used):
- `alt_line_70_eval --check-only`: exit 2, 57 checks. `alt_parlay_x_eval --check-only`: exit 2, 73
  checks.
- Both fail only the three ALT_LADDER_HIST freeze checks: no prereg commit; 0 distinct shas; not
  FROZEN.

Verification: ruff 0, format 0, mypy 0 (`alt_ladder_hist_gate.py` and its test); pytest 62 passed,
exit 0 (the four alt test files, `--no-cov`).

Not verified: outcomes (none read by design); whether the williamhill_us, bovada and betmgm flagged
ladders share the DraftKings cause; the cosmetic UTC note.

Remaining risk, now small: one fixed book at a single snapshot about T-64. The words are read "at the
DraftKings t60 snapshot".

Verdict: APPROVED
