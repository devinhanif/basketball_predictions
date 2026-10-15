# INJURY_TIMING: does matching the training report snapshot to the one served change production props CRPS? (pre-registration)

Status: **DRAFT revision 1, not frozen, and NOT FREEZABLE TODAY: its own minimum-data gate fails on the data we hold** (section 7). Written by the modeler Thu Oct 15 2026; gate clock `date` output `Thu Oct 15 13:05:38 CDT 2026` (start) and `Thu Oct 15 13:05:39 CDT 2026` (end). No arm of this rule has been fitted or scored on a real outcome; the harness has been run end to end on synthetic outcomes only.
- **How it becomes the rule:** only after the data repair of section 8 passes the gate on the repaired table. Then the main session reads it with Devin and commits with `git commit --allow-empty -m "prereg(INJURY_TIMING): <sha256>"` of everything above the line `=== RESULTS BELOW ===`:
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/INJURY_TIMING.md | shasum -a 256`.
  A harness pin commit `harness(INJURY_TIMING): <pin>` (from `python -m research.eval.injury_timing_rule pin`) must follow it and precede any real outcome.
- **Advice or momentum:** follows advice: DECISIONS 2026-10-15 12:49 CDT item 1 (Devin: "fix injury snapshot timing", measure first, then one frozen rule); Claude's own scoping note recommended the descriptive step first (`docs/research/questionable_status_scope_2026-10-15.md`, recommendation 2).
- **Measurement behind it:** `docs/research/injury_timing_measurement_2026-10-15.md` (scripts `research/scratch/injury_timing_{descriptive,rows,tagshift}.py`, tables `reports/injury_timing/*.json`).
- **Harness (exists, pinned at the freeze):** `research/eval/injury_timing_eval.py` (`check`, `rehearse`, `run`), `injury_timing_gate.py`, `injury_timing_rule.py`; pilot probe `injury_timing_pilot_probe.py` (network, not part of the pin). `run` refuses without the `prereg(INJURY_TIMING)` commit and the harness pin commit, runs the preflight before any outcome is read, and refits A0 first (G4) before fitting S or M.

## 1. Question

Production's training frame takes, per game, the latest official-report stamp with `as_of <= real tip - 60 min`. In 2023-24 the only stored stamps are 11:00 and 17:00 ET (a backfill artifact of two anchors), so that snapshot has a median lead of 180 minutes (p10 / p90 120 / 300; only 3.9% / 4.9% of games have lead <= 90). The live 2026-27 pull probes 15-minute slots and will use a fresher report. **If production props are trained on lists that are about two hours staler than the list served, does training on the same selection that is served (the freshest stamp <= tip - 60 from the real intraday sequence) lower integer-support CRPS?**

## 2. Arms (three, one function builds every list)

Rows: production's, i.e. played player-games with >= 5 prior played games, stats pts, reb, ast, fg3m, seasons 2023 and 2024. The report enters production only as the OUT set of the used snapshot (`ReportTriggerConfig(statuses=("out",))`), so that is all that changes.
* **A0** (production as backtested): list S from the stored table, in training and in the test block. Reproduces the pinned stored components.
* **S** (the live analog; the status quo after opening night): fit on S, test block features from list R (the repaired table, latest stamp <= tip - 60). This is what live does: a model trained on stale lists served a fresher list.
* **M** (the fix): fit on R, test block features from R. Training and serving use the same selection.
Same rows, same month-block walk-forward, same config (`configs/context_residual.yaml`, `configs/mov_elo_tuned.yaml`), same seeds, same calibration window, same integer map. Only `flagged` (the game -> OUT set) differs, built by the production function `flagged_from_availability` on the stored or the repaired table.

**Why M and not "make serving use the training lag":** serving a deliberately old report is arm A0 at serve time. It throws away information that the measurement shows is large (2021-22 hourly season: 37.7% of Q/D players at T-180 were OUT by T-60, n 2,082; about 0.5 rotation OUTs per game are added in the last two hours) and cannot be done for a live slate without discarding the freshest report on purpose. The information-correct fix is to give the model the same freshness in training; M vs A0 (flag `INFO_GAIN_M_VS_A0`) tells whether M beats the lag-matched alternative.

**Information or architecture?** Information. No capacity is added; the only change is how fresh the pre-tip OUT list is in training. Exposure, stated plainly: nothing in this rule has been scored on a real outcome. The aggregate error table in the measurement note (teammates of a rotation Q/D: pts CRPS +0.129 [+0.082, +0.177] vs other rows, n 20,464) was read, which shows rows with an uncertain list are worse, not what a fresher list is worth.

## 3. Metrics, floors, intervals

* **Primary:** per-row integer-support CRPS (199-point grid, `ceil(q - 0.5)`, the production map) of M minus S, per stat, paired by row, game-clustered percentile bootstrap, B = 2000, seed 2026 + 10 * season + stat index, one resample shared by the three contrasts of a (season, stat). One-sided p of H1: mean < 0 is (1 + #{boot mean >= 0}) / (B + 1).
* **Practical floor:** M minus S <= -0.005 per stat (the project's CRPS floor). Not a p-value alone.
* **Secondary contrasts (reported, flagged):** M minus A0 (value of the fresher list under matched training, against the lag-matched alternative) and S minus A0 (cost or gain of serving the fresher list to a stale-trained model).
* **Minutes:** the brief asked for minutes as a second metric. The harness does **not** score minutes; the minutes path is a different builder (`research/props/minutes_v2.py`). Declared limit: if the word is `FIX_WORTH_IT` on the four count stats, minutes get a new id; a null here says nothing about minutes.
* **Multiplicity:** one family of 4 (the four stats' M minus S one-sided tests in the report season 2024), Benjamini-Hochberg at 0.05. Replication in 2023 is a per-stat requirement, not a second family. No other contrast is in a word.
* **Select 2023 / report 2024.** There is nothing to select (no tuned constant), so 2023 is the replication season and 2024 decides. 2023 trains on about one season (playbook: a 2023-only fail on a tree is weak evidence; here 2023 must only agree in sign and CI, a stated, weaker use).
* **Slices (declared; computed in the result note, in no word):** stat; affected rows (the team's OUT set differs between S and R) versus unaffected; tip hour; min10 cell. The harness computes stat and season only.

## 4. Decision rule (frozen at the freeze; `words()` implements exactly this)

Per stat s in {pts, reb, ast, fg3m}: P1(s) = 2024 M minus S point <= -0.005 and CI upper < 0 and BH q <= 0.05. P3(s) = 2023 M minus S point < 0 and CI upper < 0. P2(s) = M minus S CI upper <= +0.001 in both seasons (no harm).
* `FIX_WORTH_IT`: at least 2 stats with P1 and P3, all four P2, guards pass.
* `PARTIAL`: exactly 1 stat with P1 and P3, all four P2.
* `GUARD_FAIL`: guards fail (any word is replaced).
* `NULL`: otherwise. A null stays a null whatever the secondary contrasts say.
* Flags: `CRPS_HARM` (a P2 fails); `SKEW_HARMS_LIVE_ANALOG` (S minus A0 CI lower > 0 in all four stats); `FRESHER_REPORT_HELPS_ALREADY` (S minus A0 CI upper < 0 in >= 2 stats); `INFO_GAIN_M_VS_A0` (M minus A0 CI upper < -0.005 in >= 2 stats).
* **What a word means for action (recommendation to Devin, not a promotion):** `FIX_WORTH_IT`: retrain production props on the repaired history (a daily-path change, Devin's call, nothing before opening night). `NULL`: do nothing; serve the freshest report as designed. `PARTIAL`: the live 2026-27 log decides. Because the fix is a data repair, not new modelling, a `FIX_WORTH_IT` also commits to keeping the intraday pull going (it does live).
* **Kill criteria:** `NULL` or `GUARD_FAIL` closes the production-props question; the one allowed follow-up is the minutes metric under a new id. No variant (other cutoffs, other statuses, Q/D as features) is tried after a result. A Q/D-tag version belongs to PPLAY_INTO_PROPS_LIVE (frozen), not here.
* **2025 touch:** none requested. The 2025 holdout stays unloaded; `collect_cross` raises on a test season > 2024 and every SQL read carries `season <= 2024`.

## 5. What each choice protects against

* Comparator S (not only A0): A0 is a backtest in which train and test agree; it is not what runs live. Comparing the fix to A0 alone would credit M with information a deployed S already has.
* The floor -0.005 with CI upper < 0 and BH: a fresher list moves a quarter of team-games, so a tiny but significant gain is plausible and would not justify a data pipeline.
* Game clustering: teammates share one game's list; row-level CIs were anti-conservative here.
* Integer support on every arm: the T114-T118 artifact.
* One function builds S-list and R-list: the only difference is the stamp chosen, so a gain cannot come from different row handling.
* A0 refit must reproduce the pinned stored CRPS (G4) before S or M is fitted: otherwise the arms are not comparable.
* No outcome before the preflight: gate, planted future and row identity are label-free.

## 6. Planted future and other guards

* **G1 planted future (label-free, in `check` and `run` preflight):** on the repaired rows, for 50 sampled games a fresh OUT row is planted stamped tip - 30 min; the used OUT set must be unchanged in every game (0 leaks) and a row planted at exactly tip - 60 must enter in every game (the test has power). Unit tests also show the check fails if the cutoff is loosened.
* **G3 row identity:** S, M and A0 must score identical (game, player) rows; `collect_cross` raises otherwise; games with no usable R snapshot are absent from the OUT dict in R exactly as production does (has_report = 0), never imputed.
* **G4 A0 reproduction:** A0 refit integer CRPS equals the pinned stored values to 1e-6 per stat and season, or the run raises before S and M are fitted. Pins (docs/prereg/MEDIAN_BIAS.md section 6, stored components of 2026-10-11): pts 3.152830 / 3.187553, reb 1.292817 / 1.314247, ast 0.905903 / 0.900215, fg3m 0.569049 / 0.592213 (2023 / 2024). **Declared unknown:** whether a LightGBM refit reproduces to 1e-6 (earlier rules reproduced from stored components, not by refitting); if G4 fails on determinism alone the rule is inconclusive, not null, and the tolerance would be a new id.
* **No past-only placebo and no shuffled-list arm.** Declared limit: S and M differ only in the list and share a function, so a pipeline-only difference is unlikely, but a placebo that replaces the fresh list with a same-lead past list would size the null. The adversary should decide whether to require one before the freeze; adding it costs four more fits.

## 7. Minimum-data gate, measured on the data the run would use (non-negotiable 8)

Measured by `research/eval/injury_timing_gate.py` on `nba.duckdb` (stored table), `season <= 2024`, real tips only. `date` output at the start of the run: **`Thu Oct 15 13:05:38 CDT 2026`**, at the end: **`Thu Oct 15 13:05:39 CDT 2026`**. Output `reports/prereg_injury_timing/gate.json`. Thresholds fixed before the run: games with a usable snapshot >= 1,200 per season; share of games whose used snapshot has lead <= 90 min >= 0.80 (so R is a near-T-60 list); games whose OUT set differs between R and S >= 300 per season.

| check | 2023 | 2024 | pass |
|---|---|---|---|
| games with a usable snapshot (>= 1,200) | 1,317 of 1,318 | 1,317 of 1,320 | yes |
| share with used lead <= 90 min (>= 0.80), **stored table** | **0.039** | **0.049** | **NO** |
| lead p10 / p50 / p90 (min) | 120 / 180 / 300 | 120 / 180 / 300 | n/a |
| distinct stamps per game | 2.55 | 2.51 | n/a |
| games whose OUT set differs from stored (>= 300) | not measurable without R | not measurable | n/a |

**Gate result: FAIL (`gate_pass false`) on the stored table. The rule is not frozen and cannot run until the table is repaired.** The OUT-set difference check and the paired-delta power are evaluated on the repaired table by the same script (`--repaired`). Power is **unknown** until then; no minimum detectable effect is claimed. The measurement note bounds the all-rows effect between about 0.001 and 0.014 CRPS (a Gaussian shift approximation with an assumed forecast sd of 5.6, exposure <= 0.27 of team-games, not a measurement), so the floor is neither excluded nor expected.

## 8. Data-capture audit and the repair (non-negotiable 8: a gate fails on data only after this)

| source | what we hold | gap or repair, with cost |
|---|---|---|
| Official report PDFs on disk, `data/availability_official` | 1,682 files 2022-10-18 .. 2026-07-19: 11AM (688), 5PM (690), 11_45AM (151), 05_45PM (152), 10AM (1). **Two per game day.** | none in the files; this is the whole intraday content we hold for 2022-26 |
| History PDFs, `data/history/availability_official` | 5,502 files 2019-10-21 .. 2022-06-16; 2021-22 hourly (24.9 stamps per game), 2019-20 about 4.7 | holds the only full intraday sequence (outside the select/report seasons; COVID-era regime) |
| NBA CDN `ak-static.cms.nba.com/referee/injury/` intraday slots 17:15 .. tip - 60 for 2023-24 | **unknown** (WebFetch timed out, agent shell has no route) | **Pilot probe, Devin's shell:** `.venv/bin/python -W ignore -m research.eval.injury_timing_pilot_probe --dates 2024-01-17 2024-02-22 2023-12-12 2024-03-05 2023-11-08` = 250 requests (dry run prints "5 dates, 250 requests"), about 3 minutes at 0.6 s, writes only `reports/injury_timing/pilot_probe.json` |
| Full re-pull if the pilot finds slots | 2023-24 game dates 417 (206 + 211 distinct stamp dates seen) | 12:00-21:45 at 15-minute slots is 50 requests per date, about 21,000 requests, about 3.5 hours at 0.6 s from Devin's shell into a **separate scratch duckdb** (`load_inputs(..., injury_db=...)` reads it; `nba.duckdb` is never written); a narrower slot set follows from the pilot. Resumable per date |
| Wayback or third-party archives of the injury report | not evaluated | not recommended without Devin (terms unread) |
| Live 2026-27 pulls (`nba/daily/injury.py`) | will log stamp and tip per game | the free, clean source of a true T-60 list; one season of it replaces the repair for forward questions |
| 2021-22 hourly as a stand-in for the select/report seasons | exists | different season, regime and rosters; not a substitute under this rule |

## 9. Playbook check

Model class: gradient-boosted trees (LightGBM context residual over a recency base); an input-freshness test, no new capacity.
1. Information or capacity: information (a fresher pre-tip fact); no model change.
2. Enough data for this class: moderate appetite (2-4 seasons); 2023 trains on about one season, so 2023 is replication only (section 3). The gate on the repaired table is the data check; it fails on the stored table.
3. Test validity: real tips (`tip_source="real"`), played-games handling inherited from production, integer support for counts, game-clustered paired CI, planted future (G1), A0 reproduction (G4), row identity (G3). Open validity risks, declared: no past-only placebo; LightGBM refit determinism at 1e-6 untested; scored rows are players who played, so a player who was listed and turned OUT never appears (T484 wound: this test sees the effect on teammates' forecasts, not on the vacated player); power unknown until the repair.
4. Lineage: this is the report-trigger feature (`latest_pretip_flagged`) with a better stamp; it is NOT a Q/D-tag test (P(play) R, PPLAY_CONTEXT T316, T482/T484, PPLAY_INTO_PROPS_LIVE), NOT the lineup T-30 lever, and NOT QUESTIONABLE_STREAK (gate FAIL, closed). Inherited guards: the NULL-pattern audit (here: has_report = 0 games are reported per season) and the planted future.
5. Scored against: integer CRPS of the whole distribution at the four count stats, not log loss at a line (T477: CRPS gains have not reached the line). A pass here would not be a betting claim.
Class checks (trees): missingness audit beyond the as-of cause: games absent from R but present in S are reported; planted future: yes; "does the arm only re-express the base": no, it changes one input list.

## 10. Priors (binary, stated before any result; for `docs/PREDICTIONS_LEDGER.md`)

Not blind: the measurement note is seen (aggregate sensitivity and the Gaussian bound).
* P(pilot probe finds >= 1 report stamped between 17:15 and 20:45 ET for >= 4 of 5 sampled 2023-24 dates): 0.65.
* P(repaired gate passes, given a full re-pull): 0.60 (fresh share >= 0.80 needs a stamp inside 60-90 minutes for most tip times).
* P(`FIX_WORTH_IT`): 0.07; P(`PARTIAL`): 0.13; P(`NULL`): 0.74; P(`GUARD_FAIL`, incl. G4): 0.06. Conditional on the repair existing.
* P(M minus S point <= -0.005 on pts in 2024): 0.12; expected M minus S pts -0.0015 (80% -0.0060 to +0.0010); expected M minus A0 pts -0.0030 (80% -0.0100 to +0.0010).
* P(`FRESHER_REPORT_HELPS_ALREADY`): 0.35; P(`SKEW_HARMS_LIVE_ANALOG`): 0.05; P(G4 passes at 1e-6): 0.80.
* P(the live 2026-27 median lead of pulled reports is <= 90 min): 0.55.

## 11. Harness contract

`research/eval/injury_timing_eval.py` (`check`, `rehearse`, `run`), `injury_timing_gate.py`, `injury_timing_rule.py`, tests `tests/eval/test_injury_timing_harness.py`. Synthetic rehearsal (no fit, no real label): truth `null` gives `NULL`, `fix` gives `FIX_WORTH_IT` (and flags `SKEW_HARMS_LIVE_ANALOG`, `INFO_GAIN_M_VS_A0`), `harm` gives `NULL` with `CRPS_HARM`; outputs under `reports/prereg_injury_timing/rehearsal/<truth>/results.json`. The real command (maintainer, from the main checkout, after the freeze and pin, with the repaired scratch DB):

    mkdir data/ops/heavy.lock || exit 1; trap 'rmdir data/ops/heavy.lock' EXIT
    .venv/bin/python -W ignore -m research.eval.injury_timing_eval check --repaired <scratch.duckdb>      # label-free preflight first
    .venv/bin/python -W ignore -m research.eval.injury_timing_eval run --sha <64 hex> --repaired <scratch.duckdb>

Cost: 18 month-block walk-forward fits (2023-24) per stat per arm, three arms, four stats (216 LightGBM fits); per-stat checkpoints (`a0_<stat>.npz`, `arms_<stat>.npz`) are written. Wall time is not measured here (watchdog); one arm is the same protocol as the 2026-10-11 component build. Dry-run counts before any fit: the preflight prints the gate verdict and the planted-future result; no outcome has been read at that point.

=== RESULTS BELOW ===
