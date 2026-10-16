# INJURY_TIMING: does training on the report list a live T-60 run would see change production props CRPS? (pre-registration)

Status: **DRAFT revision 3, not frozen, and NOT FREEZABLE AS WRITTEN: the decisive 2024 exposure check of its own gate fails (197 games against 300, section 7).** Revision 2 (commit f3ed6651, text sha 48c59d27) was BLOCKED by the pre-freeze review (`docs/reviews/INJURY_TIMING_prefreeze_2026-10-15.md`): the repaired table leaked about 30 minutes of future into two thirds of games (B1), S did not serve what live serves (B2) and the gate failed for the live design in 2023 (B3). Written by the modeler Thu Oct 15 2026; the gate was re-run on the new table (section 7, `date` output quoted there). No arm of this rule has been fitted or scored on a real outcome; the harness has been run end to end on synthetic outcomes only. Devin chose option (a) of B3 (Claude's pick) on 2026-10-15; he confirms before any freeze.
- **What revision 3 changes (all before any outcome; each answers one finding):** B1 known-at is the time printed in the PDF header (hour-only files are +30 min), re-parsed into a NEW scratch db with a pinned sha, stored 11:00 / 17:00 rows shifted the same way; B2 one cutoff function, `live_cutoff`, used for S at test time, for M in training and test, for P at test time and in the gate; B3(a) 2024 decides, 2023 is "point < 0, reported" and its exposure check is informational; M1 a past-only placebo arm P, and M minus P must also pass; M2 every number-moving file is pinned and a per-game used-stamp / OUT-set fingerprint is pinned; M3 library versions are pinned and logged; M4 every word has a world, flags have threshold tests, a failed guard is an outcome (`GUARD_FAIL`), not a crash; M5 the power and reachability text and restated priors; M6 every number from the mis-stamped clock is restated; m1-m4.
- **How it becomes the rule:** the main session reads it with Devin and commits with `git commit --allow-empty -m "prereg(INJURY_TIMING): <sha256>"` of everything above the line `=== RESULTS BELOW ===`:
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INJURY_TIMING.md | shasum -a 256`.
  A harness pin commit `harness(INJURY_TIMING): <pin>` (from `python -m research.eval.injury_timing_rule pin`) must follow it and precede any real outcome. The pre-freeze review's steps 5-8 are still to run (adversary re-check of rev 3, freeze, pin, heavy run).
- **Advice or momentum:** follows advice (DECISIONS 2026-10-15: Devin "fix injury snapshot timing"; B3 option (a), Claude's pick, Devin said go).
- **Measurement behind it:** `docs/research/injury_timing_measurement_2026-10-15.md` (sections 1-12 are on the mis-stamped clock and are superseded where section 13 says so; section 13 is the corrected-clock table).
- **Pinned inputs** (the harness refuses on any mismatch; `check` prints them):
  * the re-parsed scratch duckdb `data/scratch/injury_intraday_v2.duckdb`, header-stamped (`research/eval/injury_timing_reparse.py` reading the unchanged raw PDFs under `data/raw/injury_intraday_2026-10-15/`; tables `player_availability`, `injury_intraday_reports` with `filename_dt`, `header_dt`, `n_unmatched`, `stored_report_headers` with the header audit of every STORED PDF dated <= 2025-07-31);
  * the per-game fingerprints (sha256 of the sorted `game|used stamp|OUT ids` lines for each list set and season; they pin `nba.duckdb`'s stored table, the schedule tips and the cutoff function together, so a daily write to `nba.duckdb` that touches 2022-24 games changes them and refuses the run);
  * every file named in `injury_timing_rule.HARNESS_FILES` (the three harness files, the re-parse script, `nba/props/context_residual.py`, `nba/props/lower_tail.py`, `nba/props/baselines.py`, `nba/models/rung0_baselines.py`, `nba/eval/lower_tail_eval.py`, `nba/eval/context_residual_eval.py`, `nba/features/injury_report.py`, `nba/features/game_tipoff.py`, `configs/context_residual.yaml`, `configs/mov_elo_tuned.yaml`) via the harness pin commit;
  * library versions: lightgbm 4.7.0, polars 1.44.2, numpy 2.5.3, scipy 1.18.1, duckdb 1.5.6 (checked in the preflight, logged in `results.json`).

```
REPAIRED_DB_SHA256: 0e64c25718ba421eff021f44d1aa3cfa2019984a9294cd8dbc5eb01bbff919b1
FINGERPRINT_S_2023: 9197be62a648a682191f157c68398834c30b685c67c0fe32b5be1e40fc5d6bb6
FINGERPRINT_S_2024: df81841aaa85e67d02e9547bec59224f3566d7448d08a975f599425d2e2a97b4
FINGERPRINT_RLIVE_2023: abb6b23a749c2373f4d23b3d83d41956d2e9ef9fa80a3c2ad64e5c3eba6438fa
FINGERPRINT_RLIVE_2024: 6d3f7666ec24a38793a433b9ba608ebd748e0780efff437a36ee0eb45fe5d776
FINGERPRINT_P_2023: 2c7559e2e7b02aa2f9a071e18e5cfd898ee206db30875057b43d504de7ca0326
FINGERPRINT_P_2024: 02031a0c5a326adc036f22af73ae0210562152d156c8dbc6baaaefd8ae02b5ba
```

## 1. Question

Production's training frame takes, per game, the latest official-report stamp with `as_of <= real tip - 60 min`. The stored table holds two stamps per game day, 11:00 and 17:00 ET, and **both are stamped 30 minutes early**: the files are named for the hour but each hour-only PDF is headed `HH:30` (190 of 190 sampled hour-only PDFs; the `HH_MM` files are exact, 30 of 30). On the header clock the stored snapshot has a median lead of 150 minutes (p10 / p90 90 / 270), not 180. The live pretip run is different: a launchd job at :20 and :50 ET, the forward log scores the latest run with `made_at <= tip - 60`, and that run uses only reports stamped strictly before itself. **If production props are trained on the stored lists but served the list a T-60-eligible live run sees, does training on that same live selection (from the header-stamped intraday sequence) lower integer-support CRPS?**

## 2. Arms (four; one list builder, one cutoff function)

Rows: production's, i.e. played player-games with >= 5 prior played games, stats pts, reb, ast, fg3m, seasons 2023 and 2024. The report enters production only as the OUT set of the used snapshot (`statuses=("out",)`), so that is all that changes. Every list is built by `injury_timing_gate.lists` (the latest stamp `<= cut[game]`, only rows of that stamp, a game with no usable stamp absent and never imputed); the arms differ only in the table and the cutoff:
* **A0** (production as backtested): fit and test on the STORED lists exactly as production has them (stored stamps, cutoff `prod_cutoff = tip - 60`). It reproduces the pinned components (G4). Training on the mis-stamped stored table is what production does; this rule states it and leaves A0 and the fit side of S that way.
* **S** (the live analog; the status quo after opening night): fit on the stored lists, test block features from **R_live**.
* **M** (the fix): fit on R_live, test block features from R_live.
* **P** (placebo, required): fit on R cut at each game's corrected STORED used stamp (the same freshness as the stored table, taken from the header-stamped intraday parse), test block features from R_live. P separates freshness from source and parse: M differs from S in the table, the parser, the 2022 warm-up rows and the freshness; P keeps everything but the freshness.
* **R** = the header-stamped intraday table UNION the stored table with every stored stamp moved to its PDF header time (a stamp with no audited header is an error). **R_live** = lists from R with cutoff `live_cutoff(tip)` = the last :20 or :50 ET run at or before `tip - 60`, minus 1 microsecond (the run probes slots strictly before itself; `made_at <= tip - 60` as in `nba/daily/settle.py`). 2022 games (the warm-up for early 2023) have no intraday stamps, so R_live equals the shifted stored lists there; M is therefore close to S in early 2023 (an attenuation by construction, not just "about one season").
Same rows, same month-block walk-forward, same config, seeds, calibration window and integer map; only `flagged` (game -> OUT set) differs.

**Why M and not "make serving use the training lag":** serving a deliberately old report is arm A0 at serve time and discards what the report moves in the last two hours (22.7% (588 of 2,595, 2023) and 24.0% (617 of 2,569, 2024) of Q/D players listed at T-180 are OUT at T-60 on the corrected clock). `M minus A0` is reported (flag `INFO_GAIN_M_VS_A0`) but is in no word.

**Information or architecture?** Information. No capacity is added; one pre-tip list changes. Exposure, stated plainly: nothing in this rule has been scored on a real outcome. The stored-table error table in the measurement note (teammates of a rotation Q/D: pts CRPS +0.129 [+0.082, +0.177] vs other rows, n 20,464) was read; it shows rows with an uncertain list are worse, not what a fresher list is worth.

## 3. Metrics, floors, intervals, power

* **Primary:** per-row integer-support CRPS (199-point grid, `ceil(q - 0.5)`) of M minus S, per stat, paired by row, game-clustered percentile bootstrap, B = 2000, seed 2026 + 10 * season + stat index, one resample shared by all contrasts of a (season, stat). One-sided p of H1 mean < 0: (1 + #{boot mean >= 0}) / (B + 1). **Placebo contrast:** M minus P with the same machinery.
* **Practical floor:** point <= -0.005 per stat (the project's CRPS floor), and the CI upper limit < 0.
* **Multiplicity:** two families of 4 in 2024, BH at 0.05: the four M minus S tests, and the four M minus P tests. A stat counts only if it passes in both (M minus P is a per-stat requirement, not an added claim). All other contrasts are reported and in no word.
* **Select 2023 / report 2024, revised (B3 a): 2024 decides.** 2023 cannot pass its own exposure bar on the live cutoff (section 7); it is reported as "M minus S point < 0" per stat and enters no word. Nothing is tuned, so there is no selection.
* **Power and reachability (M5; the CI is not the constraint, the floor is).** The review measured the game-clustered SE of a paired per-row integer-CRPS delta between two production-protocol arms on the same rows (`reports/minutes_v2` A1 minus A0, seasons <= 2024) as the nearest proxy on disk: 27,583 rows / 1,315 games in 2024, MDE80 (2.80 x SE) about 0.0037 (pts), 0.0016 (reb), 0.0013 (ast), 0.0007 (fg3m). That proxy is not this contrast and no MDE is claimed for it. The binding fact is the floor: -0.005 is 0.55% of ast CRPS (0.90) and 0.85% of fg3m (0.59), and on the live cutoff only 197 of 1,303 compared games (15%) change their OUT set in 2024, so the effect inside the changed games would have to be about -0.03. The nearest proxy move (a minutes-input change) was pts -0.0018 [-0.0044, +0.0008] and ast -0.0005. `FIX_WORTH_IT` needs two stats under the floor in both families and is close to unreachable; `PARTIAL` (pts) is the realistic ceiling; a `NULL` means "smaller than -0.005" while the M minus S CI upper limit stays above -0.005, and must not be read as "no effect".
* **Minutes:** the brief asked for minutes as a second metric. The harness does **not** score minutes (a different builder, `research/props/minutes_v2.py`). Declared limit: a null here says nothing about minutes; a `FIX_WORTH_IT` on the count stats opens a minutes test under a new id.
* **Slices (declared; computed in the result note, in no word):** stat; affected rows (the team's OUT set differs between S and R_live) versus unaffected; tip hour; min10 cell. The harness computes stat and season only.

## 4. Decision rule (frozen at the freeze; `words()` implements exactly this)

Per stat s in {pts, reb, ast, fg3m}, 2024: P1(s) = [M minus S point <= -0.005 and CI upper < 0 and BH q <= 0.05] AND [M minus P point <= -0.005 and CI upper < 0 and BH q <= 0.05]. P2(s) = M minus S CI upper <= +0.001 (no harm).
* `FIX_WORTH_IT`: at least 2 stats with P1, all four P2.
* `PARTIAL`: exactly 1 stat with P1, all four P2.
* `NULL`: otherwise. A null stays a null whatever the secondary contrasts say.
* `GUARD_FAIL`: a failed preflight (versions, db pin, G0, gate, planted future, fingerprints) or G4 is **an outcome**, written to `results.json` with its reason; no S, M or P is fitted after a G4 failure and no outcome is read after a preflight failure. A refused freeze or pin is a refusal (raises), not a word.
* Flags (in no word): `CRPS_HARM` (a P2 fails); `PLACEBO_EXPLAINS_GAIN` (a stat passes against S but not against P); `SKEW_HARMS_LIVE_ANALOG` (S minus A0 CI lower > 0 in all four stats); `FRESHER_REPORT_HELPS_ALREADY` (S minus A0 CI upper < 0 in >= 2 stats); `INFO_GAIN_M_VS_A0` (M minus A0 CI upper < -0.005 in >= 2 stats). Reported per stat: 2023 M minus S with point < 0 or not.
* **What a word means for action (recommendation to Devin, not a promotion):** `FIX_WORTH_IT`: retrain production props on the repaired history (Devin's call; nothing before opening night). `NULL`: do nothing. `PARTIAL`: the live 2026-27 log decides.
* **What a failed gate means (section 7: 2024 exposure 197 < 300).** The bar is not lowered here. Options for Devin, with Claude's pick first: (c) close INJURY_TIMING as a data failure on the production-props question and let the 2026-27 live log (it records stamp, made_at and tip) answer it; this costs no fits and loses little, because the expected effect inside 15% of games would have to be about -0.03 to reach the floor; (a') Devin sets a new exposure bar with a written power rationale before anything is run, the rule is re-hashed and the gate re-run on the same table; (b) none other. A freeze on the table as it stands would be a freeze that cannot pass its own gate (non-negotiable 8).
* **Kill criteria:** `NULL` or `GUARD_FAIL` closes the production-props question; the one allowed follow-up is the minutes metric under a new id. No variant (other cutoffs, other statuses, Q/D as features) is tried after a result. A Q/D-tag version belongs to PPLAY_INTO_PROPS_LIVE (frozen), not here.
* **2025 touch:** none requested. The 2025 holdout stays unloaded; `collect_cross` raises on a test season > 2024, every SQL read carries `season <= 2024`, and the re-parse refuses any PDF dated after 2025-07-31.

## 5. What each choice protects against

* Header-clock known-at (B1): the same failure class as N15 one layer down (a wrong report clock admitting future). Without it M would learn tip-30 lists live can never see.
* One live cutoff (B2): S must serve what live serves; otherwise "the fix" is compared with a serving rule nobody runs.
* Placebo P (M1): M differs from S in source and parse; P isolates freshness. A gain that P matches is a parser or source gain, not a timing gain (`PLACEBO_EXPLAINS_GAIN`).
* 2024 decides (B3): 2023 cannot pass its exposure bar, so it must not veto; it is shown so a sign flip is visible.
* Game clustering: teammates share one game's list. Integer support on every arm: the T114-T118 artifact. One list builder: a gain cannot come from different row handling. A0 reproduces the pinned stored CRPS (G4) before S, M or P is fitted. Pinned files, versions and fingerprints: a drift in `nba.duckdb`, a config or a library changes an arm silently otherwise.

## 6. Guards

* **G0 header audit (label-free, B1/m3):** every re-parsed report's stored stamp equals its PDF header time, and header minus filename is +30 (hour-only) or 0 (`HH_MM`); no error rows; every STORED PDF dated <= 2025-07-31 audited the same way. Any mismatch refuses the run. The re-parse already refuses to load a file that breaks the convention. G0 is the guard that would have caught rev 2.
* **G1 planted future:** on R, in 50 sampled games a fresh OUT row is planted 1 second after the live cutoff (it must not enter in any game) and one exactly at the cutoff (it must enter in every game); an independent invariant checks that no used stamp lies after `tip - 60`. G1 tests the list builder and the cutoff, not the stamp clock (that is G0).
* **G3 row identity:** A0, S, M and P score identical (game, player) rows; `collect_cross` raises otherwise.
* **G4 A0 reproduction:** the A0 refit equals the pinned stored integer CRPS to 1e-6 per stat and season (pins from docs/prereg/MEDIAN_BIAS.md section 6: pts 3.152830 / 3.187553, reb 1.292817 / 1.314247, ast 0.905903 / 0.900215, fg3m 0.569049 / 0.592213, 2023 / 2024), else `GUARD_FAIL`. The review confirmed all 8 pins reproduce and that a refit of pts block 0 is exact (1 of 72 (stat, block) refits; the rest not verified).
* **Fingerprints, versions, file pins (M2, M3):** see "Pinned inputs".
* Declared limits: no shuffled-list arm (P is the placebo); the unmatched-name count is recorded per file (`n_unmatched`) but not capped here: 4,811 unmatched name entries over 4,194 files; 3 players' OUT rows are never matched by the intraday resolver (review check 11), so M can miss them while S's stored parse has them; P carries the same parse and therefore shares that loss.

## 7. Minimum-data gate, measured on the data the run will use (non-negotiable 8)

`research/eval/injury_timing_gate.py` on R_live, the stored lists and P, `season <= 2024`, real tips. `date` output: start **`Fri Oct 16 00:22:57 CDT 2026`**, end **`Fri Oct 16 00:22:59 CDT 2026`**. Output `reports/prereg_injury_timing/gate.json`.
**Thresholds and what changed (before any outcome):** rows with a usable snapshot >= 1,200 per season (unchanged); OUT sets differing from the stored lists >= 300 in the **decisive** season 2024 (2023 informational, B3 a); share of games whose used lead is <= **120** min >= 0.80. The lead bar moved from 90 to 120 because the live cutoff (:20 / :50 runs, hourly reports stamped :30) puts the used lead at 90 or 120 by construction; the rev 2 bar (<= 90) is shown beside it. Both are in `gate.json`.

| check | 2023 (informational) | 2024 (decisive) | pass |
|---|---|---|---|
| games with a usable live-cutoff snapshot (>= 1,200) | 1,316 (of 1,318) | 1,306 (of 1,320) | yes |
| share with used lead <= 120 min (>= 0.80) | 0.998 | 0.994 | yes |
| same share at the rev 2 bar, lead <= 90 min | 0.669 | 0.654 | (not the bar) |
| used lead p10 / p50 / p90 (min), R_live | 90 / 90 / 120 | 90 / 90 / 120 | n/a |
| stored snapshot lead on the header clock p10 / p50 / p90 (min) | 90 / 150 / 270 | 90 / 150 / 270 | n/a |
| stored games whose used report is after tip - 60 (production leak) | 3.8% | 4.8% | n/a |
| distinct stamps per game, R (stored was 2.55 / 2.51) | 15.4 | 15.1 | n/a |
| **games whose OUT set differs from the stored lists (>= 300)** | **174** of 1,316 | **197** of 1,303 | **NO (2023 informational; 2024 decisive)** |
| P (placebo) OUT sets differ from the stored lists | 0 | 0 | n/a |

**Gate result: FAIL (`gate_pass false`) on the decisive 2024 exposure check, 197 against 300.** All other checks pass. What happened between the two numbers one could expect (322 for 2024, the review's figure and my first run of this table): every list is restricted to players with a box-score row in a season <= 2024 (`known_players`), because the intraday resolver cannot match three 2025-26 debutants (Topic, Holmes II, Jones-Garcia) that the stored parse resolved later with information from after the window, and no feature can read a player with no history. Unfiltered, 2024 was 322 and the gate would have passed on a difference no feature can use; the restriction costs 125 games of apparent exposure. The 300-game bar was set in rev 1 before any repaired data was measured and is **not lowered here**: lowering it after seeing 197 would be a rule change made on a result. The rule is therefore **not freezable as written**; the choices are in section 4 ("What a failed gate means").
P equals the stored lists exactly after the restriction (0 games differ), so the placebo contrast M minus P equals M minus S in expectation and the placebo adds no information on this table; it stays in the rule as a guard that costs 72 fits and is declared redundant here.


Audit (NN8): the hourly cadence is all the CDN published for 2023-24; nothing finer exists to buy; 2021-22 hourly is another season and regime. The exposure shortfall (2023: 174; 2024: 197) is therefore a data property, not repairable: the CDN published hourly reports, and the live cutoff makes the used list differ from the stored one in about 13-15% of games. Power is not computed (section 3).

## 8. Data-capture audit

| source | what we hold | gap or repair, with cost |
|---|---|---|
| Stored PDFs `data/availability_official` | 1,682 files; 11AM / 5PM hour-only (headed `HH:30`) and `HH_MM` from 2024-25 | audited against their headers (all dated <= 2025-07-31), used as the shifted stored rows of R; cost none |
| Intraday PDFs `data/raw/injury_intraday_2026-10-15` | 4,194 files, hourly 12:00-21:00 on every 2023-24 game date, write-once | re-parsed with header known-at into the v2 scratch db (G0); `nba.duckdb` untouched |
| v1 scratch db `data/scratch/injury_intraday.duckdb` | stamped `HH:00` (30 minutes early); sha 24f0e1a5 | superseded; never an input of this rule |
| CDN :15 / :30 / :45 slots | the pilot found hourly only | none exist to pull |
| History 2019-22 hourly | exists, other seasons and regime | not a substitute |
| Live 2026-27 pulls | will log stamp, made_at and tip | the clean forward test of the live cutoff |
| Production ingest stamp fix | a steward task fixes the same +30 min offset in production ingest (coordination by reading only) | until it lands, production trains on the mis-stamped stored table; this rule keeps A0 and S's fit side that way and says so |

## 9. Playbook check

Model class: gradient-boosted trees (LightGBM context residual over a recency base); an input-freshness test, no new capacity.
1. Information or capacity: information (a fresher, correctly clocked pre-tip fact).
2. Enough data for this class: moderate appetite; 2022 has no intraday stamps, so 2023 is attenuated by construction (section 2) and does not decide; 2024 trains on two seasons.
3. Test validity: real tips; the live serving rule (B2); header clock (B1, G0); played-games handling inherited; integer support; game-clustered CIs; placebo P; planted future; G4. Open risks, declared: scored rows are players who played (a player listed and turned OUT never appears; the test sees teammates' forecasts); unmatched names; whether the pretip run publishes a :30 report on time (the header is treated as known-at; :20 / :50 runs leave 20-50 minutes of slack); LightGBM refit determinism beyond pts block 0; power unknown (section 3).
4. Lineage: the report trigger (`latest_pretip_flagged`) with a better stamp and a live cutoff; N15 (wrong tip clock) is the ancestor, this is a wrong report clock. Not a Q/D-tag test, not the lineup T-30 lever, not QUESTIONABLE_STREAK.
5. Scored against: integer CRPS of the whole distribution at the four count stats, not log loss at a line.
Class checks (trees): missingness (games absent from R but present in S are reported); planted future; does the arm only re-express the base: no, one input list changes.

## 10. Priors (binary, stated before any arm is scored; for `docs/PREDICTIONS_LEDGER.md`)

Supersede the rev 2 priors (written 2026-10-15 16:3x CDT: FIX_WORTH_IT 0.12, PARTIAL 0.18, NULL 0.64, GUARD_FAIL 0.06; M minus S pts <= -0.005 0.20), which assumed an exposure of 47% / 60% of games that was mostly the leak, and the rev 3 first draft (FIX_WORTH_IT 0.03, PARTIAL 0.10, NULL 0.77, GUARD_FAIL 0.10), written before the debutant restriction cut 2024 exposure from 322 to 197 games. Not blind: the review's exposure (321 / 517 on the corrected clock, 174 / 322 on the live cutoff), my own filtered 174 / 197, the MDE proxies and the corrected-clock tables are seen; **no arm has been scored and A0 was not re-scored here.**
* P(`FIX_WORTH_IT`) 0.02; P(`PARTIAL`) 0.06; P(`NULL`) 0.82; P(`GUARD_FAIL`, incl. G4 and preflight) 0.10. All conditional on the rule being run at all, which the failed gate (197 < 300) does not currently allow.
* P(M minus S point <= -0.005 on pts in 2024) 0.08; expected M minus S pts -0.0010 (80% -0.0045 to +0.0010); expected M minus P pts -0.0010 (80% -0.0045 to +0.0010; P is the stored lists, so this equals the line above in expectation); expected M minus A0 pts -0.0015 (80% -0.0080 to +0.0020).
* P(M minus S point <= -0.005 on any of reb, ast, fg3m in 2024) 0.02.
* P(`PLACEBO_EXPLAINS_GAIN`, given a pts gain against S) 0.10 (P equals the stored lists on this table); P(`CRPS_HARM`) 0.05; P(`FRESHER_REPORT_HELPS_ALREADY`) 0.30; P(`SKEW_HARMS_LIVE_ANALOG`) 0.03; P(`INFO_GAIN_M_VS_A0`) 0.05.
* P(G4 passes at 1e-6) 0.80; P(preflight passes first time on the final table) 0.85.
* P(the live 2026-27 median lead of the T-60-eligible run is <= 90 min) 0.60.

## 11. Harness contract

`research/eval/injury_timing_eval.py` (`check`, `rehearse`, `run`), `injury_timing_gate.py`, `injury_timing_rule.py`, `injury_timing_reparse.py`; tests `tests/eval/test_injury_timing_harness.py`. Synthetic rehearsal (no fit, no real label): truths `null` -> `NULL`, `fix` -> `FIX_WORTH_IT`, `harm` -> `NULL` + `CRPS_HARM`, `partial` (only pts moves) -> `PARTIAL`, `placebo` (P as good as M) -> `NULL` + `PLACEBO_EXPLAINS_GAIN`; outputs `reports/prereg_injury_timing/rehearsal/<truth>/results.json`. Flag thresholds and the guard outcome are unit tested. The real command (maintainer, from the main checkout, after the freeze and pin, under the heavy lock):

    mkdir data/ops/heavy.lock || exit 1; trap 'rmdir data/ops/heavy.lock' EXIT
    .venv/bin/python -W ignore -m research.eval.injury_timing_eval check --repaired data/scratch/injury_intraday_v2.duckdb
    .venv/bin/python -W ignore -m research.eval.injury_timing_eval run --sha <64 hex> --repaired data/scratch/injury_intraday_v2.duckdb

Cost: 18 month-block fits (2023-24) per stat per arm, four arms, four stats (288 LightGBM fits); per-stat checkpoints (`a0_<stat>.npz`, `arms_<stat>.npz`). Wall time not measured here (watchdog).

=== RESULTS BELOW ===
