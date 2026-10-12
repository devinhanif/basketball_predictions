# QUESTIONABLE_STREAK: does a run of "questionable but played" games, or a same-day upgrade, sharpen P(play)? (pre-registration)

Status: **DRAFT (written by Mon Oct 12 13:58 CDT 2026, clock read by `date`), modeler. Not frozen. The minimum-data gate FAILS (too few streak rows; no plausible effect reaches the floor on all rows); the modeler recommends NOT freezing.** Queue item 3 of DECISIONS 2026-10-12 13:27 CDT. If Devin still wants it, the main session commits `prereg(QUESTIONABLE_STREAK): <sha256>` BEFORE any arm is fitted. Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/QUESTIONABLE_STREAK.md | shasum -a 256`. Seasons: 2023 select, 2024 report; 2025 never loaded.

## The rule in five lines (as it would be frozen; not recommended)
1. Candidate Q = P(play) R (RECENT_ROLE_HOLDOUT construction: C' + five recent-role columns; month-block walk-forward, production LightGBM, seed 0) + `q_streak` + `q_change` (below). Comparator R, refit in the same harness and reproduced to 1e-9 against `data/ops/recent_role_cache/scores.npz` first.
2. Rows: R's rows (rostered, `n_played_prior >= 5`, not OUT at real-tip T-60; 33,093 / 33,127). Label played = minutes > 0.
3. Metric: P(play) log loss, Q minus R, game-clustered percentile CI (2000 draws, seed 0), floor -0.005 on all rows, CI upper < 0, select 2023 / report 2024, both must pass. Questionable-listed rows are a declared slice (reported; floor -0.005 there is descriptive).
4. Guards: past-only placebo (Q - PL <= -0.003), noise-column control and shared-permutation shuffle sized from a permutation null (K >= 50 per season, before the freeze), ECE no worse by > +0.005, slices (questionable, probable, bench, starters; n >= 300) no worse by > +0.01.
5. A pass is a candidate; Stage 2 = the 2026-27 forward P(play) shadow log. Null closed; no follow-up.

## Hypothesis
A player who has been listed questionable and played in consecutive games is a "standing questionable" (managed injury) and plays tonight more often than a fresh questionable; a same-day downgrade or upgrade between the morning and the T-60 snapshot carries direction. R already holds the T-60 tag (`q60`, `d60`) and morning/evening report flags (PPLAY_CONTEXT terms), so the question is what the history of the tag adds.

## Forking paths and data exposure
No descriptive effect for the streak or the change has been looked at (the brief cited none). Seen: counts of the two columns, the per-row sd/deff of the stored R-minus-C' log-loss deltas (T455, frozen and scored), and outcome-free KL scenarios. No Q arm.

## Features (as-of; `research/eval/questionable_streak_gate.py`, unit-tested)
* `q_streak`: consecutive team games immediately before g in which i was `questionable` on that game's latest snapshot stamped <= real tip - 60 min (`player_availability`, source `nba_official_report`) AND played (minutes > 0, `player_game_stats` of those earlier games). The walk stops at the first earlier game failing either.
* `q_change`: status code on g's latest T-60 snapshot minus the code on the earliest snapshot of g (stamped <= tip - 60) that lists i (unlisted 0, available 1, probable 2, questionable 3, doubtful 4, out 5); 0 if listed once or never.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 13:54 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.questionable_streak_gate` (read-only, `season <= 2024`; JSON `reports/prereg_questionable_streak/gate.json`).
* Rows 33,093 / 33,127; team attached for all. **Questionable rows in R's population: 1,319 / 1,345** (about 65-68% of the questionable players the report lists; the rest have no box row or < 5 prior games, see EXPECTED_ABSENCE). `q_streak >= 1`: **227 / 105**; `q_streak >= 3`: 69 / 4. `q_change` nonzero on all rows: 515 / 504 (upgrades 498 / 489), on questionable rows 31 / 40.
* Per-row sd (stored R - C'): all rows 0.127 / 0.120, deff 1.39 / 1.33, half-width 0.0016 / 0.0015; questionable rows 0.121 / 0.126, half-width 0.0067 / 0.0068.
* Expected effect (no descriptive exists; scenario = streak rows play 0.10 more often than R says, KL): all rows **-0.0003 / -0.0001** -> power about 0 against the -0.005 floor; questionable slice -0.0081 / -0.0032 -> power 0.66 / 0.15 (about 0.10 for both seasons), on 227 / 105 streak rows.
* Gate checks: half-width all rows PASS; power at the scenario FAIL both seasons; `q_streak >= 1` on >= 300 questionable rows FAIL (227, 105).

## Gate verdict and recommendation
**FAIL.** The streak is rare (105-227 rows a season) and R already prices the tag; even a 10-point shift on every streak row is 1/15 of the all-rows floor. Recommend not freezing. The 2024 drop in long streaks (4 rows with >= 3) against 69 in 2023 is unexplained (report format or team behaviour); recorded, not chased.

=== RESULTS BELOW ===

(not run)
