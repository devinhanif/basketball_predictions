# AVG_SHADOW_LIVE pre-freeze review (adversary, 2026-10-13)

Object: the DRAFT rule `docs/prereg/AVG_SHADOW_LIVE.md` (commits 12901b9, 08bee07), the gate script
`reports/prereg_avg_shadow_live/gate.py` and `gate.json`. No result exists; this attacks the rule.
Data read: only the season-2024 rehearsal rows `reports/prereg_seq_full_mix_rt/rehearsal/descriptive/rows.parquet`
(every script asserts no date after 2025-06), `nba.duckdb` read-only for live 2026 row counts, and code.
Not opened: the T478 OOF file, any season-2025 row, `data/history/`.

Model class: neural sequence model (W_ALL E2 transformer, blended with A0 by the T442 gate) mixed 50/50 with a
gradient-boosted residual model (RECENT_ROLE_FULL).

## Verdict summary
The statistical core is sound: n_min reproduces, the week-12 floor binds in every supply scenario, the guards
(d)-(f) and G2/G3 rehearse comfortably on 2024, postseason routing is pre-tip, and the canonical dump does exclude
the AVG tables. Seven items would let the frozen text fail on its own definitions, drop rows selectively, or leave
an outcome or a holdout use undefined. Each has a one-paragraph fix below. Nothing here argues against the shadow.

## Checks

| # | Check | Command / source | n | Number (CI) | Result |
|---|---|---|---|---|---|
| 1a | n_min re-derived | by hand from gate.json: sd 0.2293, deff 1.072, h 0.005, sigma_F 0.0010 | 25,767 pts rows, 1,230 games (2024 `002`) | 6,842 -> 6,900 | PASS |
| 1b | Date-clustered design effect (not measured in the gate) | `.venv/bin/python -I scratchpad/date_deff.py rows.parquet` exit 0 | 25,767 / 163 dates | pts AVG-FULL -0.01610, game CI [-0.01895, -0.01310] deff 1.072; date CI [-0.01907, -0.01328] deff 1.048 | PASS |
| 1c | Half-width at the look, incl. FULL's 2-week burn-in and date deff up to 1.16 (FULL rule's worst) | same script | n 6,900 / 7,203 / 8,722 / 10,470 | one-sided hw 0.00498-0.00516 / 0.00489-0.00506 / 0.00449-0.00465 / 0.00416-0.00430 | PASS with note (pessimistic supply under a 1.16 date deff makes FAIL unreachable at week 12; INCONCLUSIVE then) |
| 1d | Live supply measured on real 2026 rows | `nba.duckdb` read-only, rows with made_at >= 2026-09-01 | forward_full_shadow 0, forward_pplay_shadow 0, forward_predictions 136 rows / 1 preseason game / 34 players | not measurable | NOT VERIFIABLE: the 85% haircut is an assumption; FULL shadow (AVG's denominator) is not live yet |
| 1e | Look reachable if n is short | rule line 5 | - | no clause | FAIL (B3) |
| 2a | Stamp and run slot | `nba/daily/replay_season.py` RUN_LEAD_MIN 70, slots :20/:50; heartbeat daily-pretip 2026-10-12 20:50:04-20:50:14 | - | >= 10 min slack between the scored run and tip-60 | PASS |
| 2b | Refit uses only settled games | rule "Refit staleness" | - | fit_through < first of month, installed_at <= made_at | PASS (gate's own fit_through not recorded: N5) |
| 2c | Rebuild = what T478 validated; proofs with tolerances and a fail action | rule P1-P4 | - | P1 conflates rebuild identity with CPU/GPU parity; no fail action; P3 fallback changes the scored object after freeze | FAIL (B4) |
| 3a | Inference: bound, clustering, BH, interim, second look | rule lines 3-6 | - | interim clustering and FAIL half-width unspecified; second look is not alpha spending | PASS with notes (N1, N2) |
| 3b | Guards rehearsable: G1 coverage on integer support | `.venv/bin/python -I scratchpad/g1.py rows.parquet` exit 0 | 25,767 pts | AVG P(y <= q10) inclusive 0.183, strict 0.065, randomised PIT 0.114 (nominal 0.10 +- 0.02); A0 0.188 / 0.068 / 0.117 | FAIL as written (B2): undefined, and the natural reading fails every arm |
| 3c | G2/G3 rehearsal | `.venv/bin/python -I scratchpad/guards.py rows.parquet` exit 0 | min<5 n 2,077; first-12-weeks min<5 n 925 | AVG-A0: min<5 -0.0155, 5-20 -0.0305, >20 -0.0241, starter -0.0238, bench -0.0265; top-team share 0.072, without it -0.0243 | PASS |
| 3d | Placebo (d) and day-shuffle (e), 7-tau approximation | `.venv/bin/python -I scratchpad/placebo.py rows.parquet` exit 0 | 25,767 | pts AVG-placebo -0.0162 (90% [-0.0184, -0.0139]); shuffle-FULL +3.03; reb AVG-placebo -0.0110 | PASS (descriptive; 7 taus not 19) |
| 4 | Missingness: failed / late / stale AVG rows | rule population line 2, coverage >= 95% | - | eligible rows without AVG are dropped, not scored as served | FAIL (B1) |
| 5 | Oracle excludes AVG tables | `nba/daily/canonical_dump.py` `canonical_frames` selects only `forward_predictions` and `forward_scores` (explicit allow-list) | - | AVG tables cannot enter the hash | PASS for exclusion; FAIL as a proof (B6): the replay degrades to `skipped`, so inference never runs under the oracle |
| 6 | Postseason / play-in route from pre-tip data | rule line 1 serving rule: `game_id` prefix `002` | - | prefix is in the published schedule | PASS |
| 7 | Decision words complete, nothing changeable after a result | rule lines 5-7, kill criteria | - | second-look outcome words and "n never reached" missing; FULL-INCONCLUSIVE case open | FAIL (B3), N8 |
| 8 | Holdout hygiene | rule header; HOLDOUT_ACCESS_LOG rows 52, 61 | - | "Nothing in this rule loads a season-2025 row" is false (rebuild trains through 2025, gate trains on 2025 OOF, P4 replays 2025) | FAIL (B7) |

Scratch scripts: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/4a3e37f1-4418-426a-8cc6-897f102142f0/scratchpad/{date_deff,guards,g1,placebo}.py`, all with the 2025 assert.

## Blocking items (each with the exact fix)

**B1. Selective drop of failed AVG rows (a NULL pattern).** The population requires an AVG row; an eligible row whose
inference failed, ran late or was skipped silently leaves the verdict, with only a 95% coverage floor. Failures
cluster where sequences are unusual (trades, call-ups, long returns), which is plausibly where AVG is worst; 5%
missing at a row harm of +0.1 moves the mean by 0.005, the whole floor. Fix: score intention-to-treat. Eligible
triple = `002` game, played, `n_prior >= 3` at the cutoff, FULL and A0 rows at the cutoff, not `stale_model`. If the
AVG row is missing, late or failed, the AVG arm is the FULL grid (delta 0), exactly what the serving rule serves.
Report the per-protocol figure descriptively. Add a missingness table: AVG-missing rate by played/DNP, minutes < 5,
starter, team win/loss, pts above/below the player's median. Any gap over 2 pp is reported and the steward explains
it in DECISIONS before the look.

**B2. G1 is undefined on integer support and fails under its natural reading.** On 2024 pts, P(y <= q10) is 0.183
(AVG) and 0.188 (A0) inclusive, 0.065 strict, against 0.10 +- 0.02. Only the randomised PIT passes (0.114). Fix:
"G1 is computed by `research/eval/ensemble_eval.guards_for` as in T478 (randomised PIT, seed 0) on the 19-atom pmf",
and P3 must also rehearse G1 on the 19-atom form for 2024. The 0.10 quantile has only 0.006 of slack offline.

**B3. Look timing and outcome words are incomplete.** (i) Line 5 fixes week 12 as Sun 2027-01-10, but "Counting
starts" says weeks move one-for-one with the start. Fix: weeks count from opening night 2026-10-20 whatever the start
date; the floors are the fixed dates Sun 2026-11-15 (interim) and Sun 2027-01-10 (final). (ii) There is no look if
pts never reaches 6,900. Fix, copying FULL_SHADOW_LIVE: if 6,900 is not reached by the last regular-season Sunday,
the final look is the end-of-season look on the same criteria, with no second look. (iii) The second look's words are
missing. Fix: PASS and FAIL as at the final look with z = 1.96 in (b) and in the FAIL half-width; still INCONCLUSIVE
means closed for 2026-27 with no serving claim.

**B4. Proofs P1-P4 lack a fail action, and P1 tests the wrong thing.** T478 saved no weights, so the Colab rebuild is
a new fit. P1 as written mixes "the rebuild reproduces T478" with "CPU equals GPU on the same weights". Its 0.0005
tolerance is tighter than one ensemble's seed sd (0.00074), so a faithful rebuild can fail it. Fix: P1a (lineage):
the rebuilt walk's 2024 regular-season pts AVG-FULL on the same 25,767 rows lies within 0.0021 (2 x sqrt2 x 0.00074)
of the stored -0.0161. P1b (serving): same weights, CPU vs GPU, median |dq| = 0, 99th percentile |dq| <= 1,
aggregate within 0.0005. Any P-proof failure means counting does not start, the steward reports, and a repair is a new
rule id; no tolerance is loosened. Remove the P3 fallback ("else the 199-point grid is stored") and the open "decided
before the freeze" storage choice. Every arm is scored from the 19 stored quantiles, full stop, and a P3 failure is a
stop. Store the AVG and SEQc 199-point pmfs as well, descriptive only.

**B5. The FULL integrity join is wrong under write-on-change.** `nba/daily/full_shadow/store.py:append_rows` skips a
row equal to the latest stored one, so a run often has no FULL row of its own. "The forward_full_shadow row of the
same run/game/player/stat" then shows mismatches or unverifiable rows. At the interim those are a kill that voids
rows. Fix: compare against the latest `forward_full_shadow` row with `made_at <=` the AVG row's `made_at`, the same
rule the draft already uses for A0.

**B6. P4 is vacuous for the code that matters.** The canonical dump's allow-list does exclude AVG tables (confirmed).
But in the replay the writer degrades to `skipped`, so the oracle never executes AVG inference, and in-place frame
mutation, connection side effects or added wall time go untested. Fix: P4 = the 20-date replay (a) with the flag on
and no weights (`skipped` checked), and (b) with fixture random-init weights (sha recorded) so inference runs. Both
must give sha256 `89889f6d`, with added pretip wall time <= 60 s per slate group. No AVG row from the replay is read,
scored or kept: the replay copy is deleted.

**B7. Holdout text is false and the use is unlogged.** The rebuild warm-trains through June 2026 on 2025 labels. The
first live gate trains on the rebuild's 2023-2025 OOF rows. P4 replays 2025 dates. Fix: replace "Nothing in this rule
loads a season-2025 row" with an exact list of these three uses. Log one HOLDOUT_ACCESS_LOG row before the rebuild,
in the systems/training class (as row 52): no 2025 AVG or SEQc forecast is scored, read or reported, and the 2025 OOF
file is consumed only by the gate-fit code.

## Non-blocking (fix before freeze if cheap)
* N1. Interim kill bound: say "game-clustered, sigma_F included" (or both clusterings). FAIL half-width = the larger of
  the game- and date-clustered sigma_F-inflated one-sided half-widths. State whether (c), (d) and the secondary p
  include sigma_F.
* N2. A 1.645 final plus a 1.96 second look is not alpha spending. Worst-case one-sided type-I at delta 0 is about
  0.04 + 0.025 with the floor. State it, or use a spending boundary. Precedent: FULL_SHADOW_LIVE.
* N3. `nba/truth/bootstrap.py` does not exist, and `nba/eval/full_shadow_live.cluster_boot` is a second CI
  implementation (multinomial weights). The AVG reader must import the one shared function, not copy it.
* N4. Supply: the FULL rule assumes a 2-week burn-in (FULL needs >= 5 prior games), while this gate assumes none. The
  official roster until 2026-11-04 changes coverage denominators, not played rows. Neither moves the look (1c), but
  state both. AVG counting cannot start before FULL_SHADOW_LIVE counting does.
* N5. Record the T442 gate's own `fit_through` and sha. Pin the W_ALL season list (2019-20 on) and the data_version in
  `forward_avg_meta`, because history now also holds 2013-15 and 2015-18.
* N6. For reb/ast/fg3m, live SEQc blends with a non-mixture A0, unlike T478. Disclose it as a construction difference
  (secondary only).
* N7. Early-month staleness: one or two days per month before Devin installs weights is about 5% stale rows, half the
  10% INCONCLUSIVE limit. Name the install deadline.
* N8. PASS proposal when FULL ends INCONCLUSIVE: say whether it is allowed. "Without FAIL or kill" currently allows it.
* N9. The freeze sha must be computed after the Status line is edited (the line sits above the marker).

## Playbook check (neural sequence + GBT residual, 50/50 pmf mixture)
1. Information or capacity: capacity (history volume). The live venue adds report timing only. The 12-week floor and
   guard (g) address the early-season concentration the playbook names (2024 first 12 weeks -0.0190, later half
   -0.0178: not concentrated on these rows).
2. Data enough: W_ALL is frozen as served. The learning curve is still rising (T485, wounded). That does not
   invalidate a test of the frozen construction.
3. Test valid: real tips, stamp, integer support and fit noise are present. Two guards break the validity rule "guards
   that cannot pass": G1 as written (B2), and missingness (B1). The raw label shuffle is correctly not used.
4. Lineage: the rebuild is a new fit of T478's procedure. Inheritance must be proved (B4 P1a), not assumed. Era
   probe flagged (T483); era channels are zero on 2026 rows.
5. Scored against: FULL and A0 by integer CRPS; no line claim. FULL itself was NOT A PASS on 2025 (shuffled-label
   guard fired, holdout row 63), so the PASS dependency on FULL_SHADOW_LIVE is necessary and stays.

## Single biggest risk
Operational missingness turning into selection: failed or late AVG inference on unusual players drops exactly the
rows where AVG loses, and the coverage floor alone would not see it (B1).

## Not verified
Live rows per game (FULL shadow has no live rows); the 19-atom forms of G1, (f) and the CRPS (P3); the CPU inference
time; date design effect inside a 12-week window; 2023 at row level (file holds 2025, not opened); whether the Cup
final is prefix `006` in this season's schedule.

## Drafted rows (not appended)
* HOLDOUT_ACCESS_LOG (before the rebuild runs): `| <date> | Colab chain rebuild for AVG_SHADOW_LIVE (E2, W_ALL, weights saved) + T442 gate fit + P4 replay | training and systems use only | all | NOT a confirmatory touch | 2025 labels used as training rows; the rebuild's 2025 OOF forecasts consumed only by the gate fit; no 2025 AVG/SEQc/FULL forecast scored, read or reported; replay copy deleted. |`
* DECISIONS: `AVG_SHADOW_LIVE pre-freeze review BLOCKED (B1-B7); freeze after the fixes, no re-review of numbers needed, adversary re-reads the diff only. What would reverse it: none; these are text fixes.`

Verdict: BLOCKED

## Re-review 2026-10-13 (revision e3a2f1e)

Read: `docs/prereg/AVG_SHADOW_LIVE.md` at e3a2f1e (`git diff 08bee07 HEAD -- docs/prereg/AVG_SHADOW_LIVE.md`, exit 0), TEST_LEDGER rows T440, T442, T465, T466. No data was opened.

| Item | Status | Where |
|---|---|---|
| B1 ITT | FIXED | line 9 (missing, late or failed AVG = FULL grid; per-protocol descriptive; missingness table with a 2 pp flag) |
| B2 G1 definition | FIXED | line 33 (`guards_for`, randomised PIT seed 0, 19-atom pmf; P3 rehearses it, line 52) |
| B3 look dates, short n, second-look words | FIXED | lines 12-13, 45 |
| B4 proofs split, fail action, no fallback | FIXED | lines 48-53 (P1a 0.0021 lineage, P1b CPU/GPU, failure = no counting + new id; P3 fallback removed) |
| B5 FULL join | FIXED | line 41 |
| B6 oracle exercises inference | FIXED | lines 53, 88 |
| B7 2025 uses listed and logged first | FIXED in text | line 4 and the drafted row at line 76; the row must be committed to HOLDOUT_ACCESS_LOG before the rebuild runs |
| N1-N9 | addressed | lines 3, 10, 12, 13, 42, 45, 84, 87, 105-107 |

**Does B7 spend 2025 for AVG?** 2025 was already spent for this model by T478 (holdout row 61), so the rebuild spends nothing new. After the rebuild, though, 2025 is in-sample for every served weight file and for the live gate. **No later confirmatory 2025 touch is possible for AVG or any refit of this lineage**, and none may ever be logged as one. Anything confirmatory comes from the 2026-27 live log, which is what this rule already says.

**Does fitting the T442 gate on 2025 OOF reopen T440/T465-T466?** No, on one condition. T440 and T465-T466 closed learned routers: E1's minutes gate, and ROUTER_G2's g' on recent-role columns, which was no better than a constant gate. The T442 gate is a different object, the learned E2-vs-A0 blend weight inside SEQc. It is part of the procedure T478 validated, and T478 also fitted it walk-forward on earlier OOF months, including earlier 2025 months. The condition: the live gate's input set and form must be T442's exactly, with no recent-role or minutes columns added. Otherwise it becomes g' again and reopens T465. (Non-blocking, recommended: record the gate's input column list and sha in `forward_avg_meta` and assert it equals T442's.)

**Introduced by the revision (blocking):**
* **R1. Late-row handling contradicts ITT.** Line 40 still says triples whose every row is later than the cutoff are "excluded; more than 2% => INCONCLUSIVE". Line 9 now scores a late AVG row as FULL. One frozen text, two answers for the same row. Fix line 40: "A late AVG row is handled by rule 2 (FULL substituted). Only a triple whose FULL or A0 row is later than the cutoff is excluded; more than 2% of eligible triples excluded => INCONCLUSIVE."
* **R2. Holdout statement contradicts the header.** Line 81 says "this rule never touches 2025", but line 4 lists three 2025 uses. Fix line 81: "this rule scores, reads or reports no season-2025 forecast; its only 2025 uses are the three training/systems uses in the header, logged before the rebuild; 2025 is in-sample for the served weights and can never be a confirmatory venue for this lineage."

**Introduced (non-blocking):**
* R3. P1a compares against -0.0161, which is a 199-point score. Say "on the 199-point integer CRPS, as the stored rehearsal".
* R4. Line 60 still says "add the steward's start delay in weeks". With fixed look dates, a late start reduces n instead. Reword to match line 45.
* R5. Line 13: an AVG PASS while FULL is INCONCLUSIVE "is reported and waits", but there is nothing to wait for, because FULL has no later look. Say "is reported; no serving proposal in 2026-27".

Commit note: this reviewer does not commit (role constraint). The main session commits this path.

Verdict: BLOCKED

## Second re-review 2026-10-13 (commit 27fcd09)

Read: `git show 27fcd09 -- docs/prereg/AVG_SHADOW_LIVE.md` (exit 0). Only the two edits were checked, plus the lines they touch.

* **R1: FIXED.** The Stamp bullet (line 40) now scores a late-AVG triple as the FULL grid under item 2, never drops it, and counts it in the missingness table. This agrees with line 9 (ITT) and with the coverage gate (line 43). A triple whose FULL or A0 row is late is not eligible under line 9, so it is not covered by the Stamp text, and that is consistent. Keeping "more than 2% late => INCONCLUSIVE" on top of ITT is a stricter operational gate, not a contradiction.
* **R2: FIXED.** Line 81 now agrees with the header (line 4) and the drafted holdout row (line 76). Non-blocking: it does not say outright that 2025 is in-sample for the served weights and can never be a confirmatory venue for this lineage. Line 4 and the re-review record that.
* No new contradiction found. R3-R5 (non-blocking) are not addressed and remain recommended.

Before the rebuild: commit the drafted HOLDOUT_ACCESS_LOG row (Devin's approval). Record the T442 gate's input column list in `forward_avg_meta` and assert it equals T442's.

Verdict: APPROVED
