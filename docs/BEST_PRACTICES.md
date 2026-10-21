# Best practices — NBA prediction system

Distilled from what worked and what broke in this repo (2026-10-05 → 2026-10-08). Each rule cites the
incident or result that earned it. Agents and maintainers follow this; CLAUDE.md remains the spec.

---

## 1. Data integrity (fix the data before the model)

| Practice | Why (incident) |
|---|---|
| **Treat DNP rows explicitly.** `player_game_stats` DNP rows have `minutes` NULL and stats 0. Exclude them from labels and averages; model P(play) separately. | 18.6% of rows were zero-stat DNPs; they biased baselines ~1 pt low and turned the "intermittent" volatility bucket into a DNP detector. Retracted T001. |
| **Season-scope anything called "season".** Partition by `(team_id, season)`; shrink early-season values to a regressed prior. | `games_into_season` was cumulative across seasons (mean 176); the tanking flag fired from game 1. |
| **Never cache empty API responses as "done".** Treat empty frames as failures and retry. | 1,202 empty box scores were silently cached for 2025-26. |
| **Reconcile parsed data against ground truth.** Hard gate: possession points equal box points per team-game (`nba.parse.reconcile.reconcile_points`; 100% of 10,558 team-games). The count formula `FGA + 0.44 FTA + TOV - OREB` is a reported diagnostic (real MAE 2.0-2.25, bias -1.6 to -2.0), not a gate. | The PBP tokenizer drops games that don't reconcile (124 of 3,951) instead of training on corrupt sequences. The old "formula MAE <= 1" gate was mis-specified (team turnovers and the 0.44 constant are not in the player box) and was unmet in every season while points reconciled; the Missi free-throw bug (79 games) was a points miss the formula hid (audit F12, F6). |
| **Name matching needs context.** Full name → suffix/diacritics → team-as-of; log unmatched; reviewed alias tables. | Last-name-only matching crashed the injury backfill (17 "Smiths"). |

## 2. Leakage (assume it until proven otherwise)

| Practice | Why |
|---|---|
| **Every feature is as-of the game:** strictly prior games; pre-tip information only, with a ≥60-minute report cutoff. | Core CLAUDE.md rule; every module ships a planted-future-row test. |
| **Gate pre-tip information on the REAL tip time in backtests too, not a proxy.** | A 19:00 ET proxy tip admitted the 17:00 injury snapshot for ~306/3,953 games (7.7%) that tipped at or before 17:00, i.e. a report published after tip (red team 2026-10-09; inflated props pts CRPS gain by ~0.003). Use `nba.features.game_tipoff` and `tip_source="real"`. |
| **Audit missingness, not just values.** A feature whose NULL pattern depends on the outcome is a leak even if its values are clean. | `opp_adjusted_ridge` was NULL iff tonight's minutes < 5. It passed the name allow-list, top-10 importance and label-correlation checks, and contaminated experiments 1–2. |
| **Suspicious gains get audited before they're celebrated.** A single feature group carrying the whole gain is a red flag. | The ablation showing the ridge "carried everything" was the leak's fingerprint. |
| **Use rosters you'd actually know.** At prediction time use projected minutes and the injury report, never box-score membership. | The sim's backtest "wins" vanished forward because the backtest saw real rosters. |
| **Honest accuracy ranges.** Pre-game NBA win models land in the mid-to-high 60s%. Treat >75% as a bug. | Published papers at 71–83% used same-game stats or random splits. |

## 3. Evaluation design

| Practice | Why |
|---|---|
| **Walk-forward by date; never random splits.** | Random splits leak future team/player strength. |
| **Pre-register the decision rule before running** (metric, floor, CI, multiplicity, slices, calibration), in the module docstring plus a doc. | Prevents goalpost-moving; caught the urge to promote per stat after seeing 2024. |
| **Select on one season, report on the next; keep a frozen holdout.** One clean touch per never-evaluated model, logged in `HOLDOUT_ACCESS_LOG.md` **before** running. | Injury-Elo and context-residual earned real confirmations this way; experiment 3's touch was voided unrun when the leak surfaced. |
| **Cluster bootstrap CIs by game** (or date for cross-game questions). | Row-level bootstraps were anti-conservative. |
| **Correct for multiplicity** (BH within declared families); keep an append-only `TEST_LEDGER.md`. | Dozens of A/Bs; only a few survived correction. |
| **Practical-significance floors** (e.g. CRPS ≤ −0.005), not just p-values. | Several "significant" effects were economically negligible. |
| **Use proper scoring rules:** log loss and Brier for probabilities, CRPS for distributions, threshold log loss for P(≥ line). Report calibration (ECE, reliability). | Parlays multiply probabilities, so calibration errors compound. |
| **Score count stats on integer support (CRPS = RPS on integers) for EVERY arm.** Comparing an integer-grid forecast (NB/Poisson) with a continuous-quantile one on integer outcomes rewards the integer grid for free. | Leak-free experiment 3's reb/ast/fg3m "wins" were 92-126% this artifact; rounding production's quantiles (`ceil(q-0.5)`) erased them (T114-T118). Likely also behind exp-2's "NB wins counts". |
| **Validate the metric itself on known-truth simulations.** | Amendment A1's coverage estimator was biased; A1.1 was chosen by simulation, not by model results. |
| **Test like a sceptic:** fair baselines (same DNP handling, same calibration), ablations, slices (cold start, starters/bench, season phase, teammate OUT). | The sim lost the fair rematch; context-residual still won against a calibrated baseline. |
| **Report negatives.** | Sim props, rung-4, RAPM, the joint set transformer and usage redistribution were all rejected honestly. |

## 4. Modelling (what actually moved the needle)

| Practice | Evidence |
|---|---|
| **New information beats new architectures.** | The two production wins both came from the pre-tip injury report. Transformers, GNN-style sets, RAPM, MLPs and stacks tied or lost. |
| **Start from strong simple baselines:** MOV-Elo, recency-weighted averages. | Elo-family models beat logistic and GBM on ~4k games; the recency average beat the sim on every prop. |
| **Residual modelling over a good baseline.** | Context-residual (GBM on actual − recency) is production props. |
| **Match the distribution to the stat.** Counts (reb/ast/3PM) → Poisson/NegBin; points (mixture of 2s/3s/FTs) → quantile/Gaussian-like. | Experiment 2: NegBin won reb/ast/3PM; Poisson failed points (bias +0.17, coverage 0.72). |
| **Don't pool stale seasons equally.** Use recency weighting or the current season. | Equal pooling degraded each added season; current-season rates gave a big CRPS gain. |
| **Tuning rarely matters; features do.** | 60-trial searches added nothing on the full data; search-set gains didn't transfer. |
| **Calibrate only where miscalibration is real and persistent** (raw ECE ≫ noise floor). | Points: calibrate. Rebounds: at the noise floor, so calibration is futile. |
| **Correlation structure:** model same-game dependence (copula); verify cross-game independence. | Same-player legs co-move (28.6% vs 20.1% hit rate); cross-game residual r ≈ 0. |

## 5. Engineering and operations

| Practice | Why |
|---|---|
| **Verify before committing:** `ruff check`, `ruff format --check`, `mypy`, `pytest`, **never piped through `tail`** (it masks exit codes). | Twice a masked failure got committed. |
| **Crash-safe long jobs:** small artifacts first, per-stage checkpoints, streamed outputs, resume on rerun. | A 95-minute run died writing one giant frame (OOM); the rework resumed cleanly. |
| **Separate data stores by writer** (DuckDB is single-writer): Kalshi DB, OOF store, paper trades. | Avoided lock contention with long read-only evals. |
| **Right hardware for the job:** trees on the local CPU (the Mac beat a T4); GPUs for large neural models. | Saves Colab units; production retrains on CPU in seconds. |
| **Registry discipline:** candidates by default; promotion is explicit, evidence-gated and human-approved; data files never go in the registry. | Promotions of injury-Elo and context-residual followed confirmation. |
| **Forward logging before tip-off** with comparison models alongside; settle afterwards. | The 2026-27 season is the only truly clean test left. |
| **Read-only markets, verified fees, defer to market without a track record.** | The parlay engine reports `no_positive_ev_found` until shadow history proves skill. |
| **Version the data, not just the code.** `make data-snapshot` before registering runs and after any load; `make data-check` flags row drops and played-vs-unplayed NULL asymmetry. | The `opp_adjusted_ridge` leak is exactly a NULL-vs-minutes asymmetry that this check surfaces mechanically (see docs/DATA_VERSIONING.md). |
| **A job that cannot start cannot alert: monitor heartbeats from an independent watchdog.** On macOS, launchd jobs touching `~/Downloads` must launch via an executable TCC allows (we use `uv`), never `/bin/sh`. | TCC blocked every `/bin/sh` launchd job (exit 126, "Operation not permitted"); scripts never started, so no alert fired until noticed by hand. `nba.ops.watchdog` (label `local.nba.watchdog`, every 10 min) checks job heartbeats and `launchctl list` exit statuses. |

## 6. Checklist for any new model

1. Write the hypothesis and pre-registered rule (and any holdout row) **first**.
2. Build features as-of; add planted-future, missingness and DNP tests.
3. Compare against the current production model **and** a strong simple baseline, with fair handling on both sides.
4. Select on season S, report on S+1; clustered CIs; BH; floor; slices; calibration.
5. If it wins, audit for leakage before believing it.
6. Register as a candidate; one logged holdout touch; promote only with approval.
7. Log it forward; let 2026-27 decide.

## Commit by explicit paths when agents share the tree (2026-10-10)

Incident: four agents worked in one checkout; the main session ran `git add <paths> && git commit` and the
commit took another agent's staged half-move (f11d95b: archive cut 1 plus half of cut 2). Two commits failed
CI on research imports until the rest landed (37ac1fd). The live path was untouched (layering test), which
is the only reason it was tidiness and not an outage. Practice: `git commit -- <paths>` only, after
`git status --short | grep -v '^??'` shows what else is staged; multi-file moves get their own worktree.

- **launchd runs the working tree.** The pretip job at 00:04 on 2026-10-11 executed an uncommitted edit to nba/daily/t30.py (its
  decision row carries `dupes_collapsed=10`) before the fix was reviewed. Edits under nba/daily, nba/lineups and ops/ must be made
  in a worktree or stashed between the :20/:50 runs until shipped; a reviewer's "it is only drafted" is not true for the live job.

- **Typed hand-backs (Devin, 2026-10-11: "yes on 2").** Every agent run ends with the fixed block in `.claude/agents/*.md`
  (status, rule, gate, changed_files, backups, drafted_rows, numbers with n and CI, unpiped verification exits, unstated
  details, cost vs ceiling, confidence with the reason it is not higher, unknowns, needs_devin). Incidents behind it
  (2026-10-10/11): placeholder ledger rows in three formats, a gate target set from a sample and revised after the full run, a
  rebuild overlapping the pretip window, "report once at the end" prose that hid a failed sub-check. Two checks now run before
  every commit of agent work: **cost-ceiling** (each run declares a ceiling in requests/credits/minutes and halts at it) and
  **ledger-complete** (rows drafted equal rows appended). Every uncaught error becomes a new check here.

- **Task routing by tier (Devin, 2026-10-11, "jev engineering").** Before delegating, the main session names the task's class and
  tier: mechanical (search, format, docs, boilerplate) → cheapest; build/data fix with a typed brief → Sonnet; frozen-rule runs
  where interpretation matters, adversary reviews, production/outward changes → strongest tier or main. Raise, never lower, inside
  a task; "undecided" goes to the main session; money/production/holdout/outward always stop at Devin. The Jev router itself is
  optional tooling (docs/research/harness_design_paper_2026-10-11.md, addendum 2).

- **Never chain an edit and its commit in one shell line (2026-10-11, twice in one evening).** A failing `assert` inside a Python
  heredoc does not stop the `;`-chained `git commit` that follows, so a doc went out "frozen" with its status unapplied and a ledger
  append went out unappended. Rule: edit → verify (hash, grep, count) → commit, as separate tool calls; `&&` after the edit, never `;`.

## Timestamps come from the clock, not from memory (2026-10-11)
Incident: an afternoon of DECISIONS headings, a ledger heading and two frozen status lines carried times 6.5 hours late ("~23:55 CT" for a
16:37 CDT freeze), because the running estimate of the time survived a context reset and was never re-read. The frozen lines cannot be
edited; the correction lives in DECISIONS 2026-10-11 17:10. Rule: before writing any time into a doc, run `date` (or take the commit
time); label the zone as the machine prints it (CDT/CST), not "CT".
Enforced (2026-10-14 retro, Devin: yes): two stamps were guessed again after the 10-13 retro. A time is written by the shell, not typed:
the doc edit takes it from `$(date "+%Y-%m-%d %H:%M %Z")` in the same command, or the line quotes the `date` output that was run just before.
A reviewer who finds a typed time with no `date` call behind it reports it as a defect.

## Locks belong to whoever made them (2026-10-11)
Incident: an agent killed its own job waiting on `data/ops/heavy.lock`, then ran `rmdir` on the lock directory; it could not rule out that the
lock was another job's. No damage (DuckDB itself refuses a second writer), but the cooperative lock stopped meaning anything. Rule: create the
lock with `mkdir`, write your pid and purpose into it, remove only a lock whose pid is yours; a stale lock (pid not running) is reported, not
removed, unless the main session decides.

## Cite commits from git, not from memory (2026-10-11)
Incident: an agent reported another rule's freeze commit as its own and the DECISIONS line repeated it; the real freeze (b5522fe) did precede
the run, but a wrong hash is exactly what would hide a freeze that came after a fit. Rule: every hash written into a doc comes from
`git log`/`git rev-parse` in the same step.

## The decision score is the headline for anything about money (2026-10-11)
Why: CRPS rewards being right everywhere; a bettor acts on a few confident legs, where the model was over-confident (said 66.7%, hit 57.0%;
reports/decision_scoring_2026-10-11.md). Rule: any result that bears on what to bet leads with the decision score of `docs/DECISION_SCORING.md`
(ROI at the actual price, with CI, against market-lean and random of the same count); accuracy metrics follow it. The morning briefing reports
it the same way once the live log has bets.

## Shuffle guards must be sized from a permutation null (2026-10-12)
Incident: RECENT_ROLE_FULL's frozen shuffled-label guard (fail if SHUF_FULL minus SHUF_CTRL <= -0.001 with a bootstrap CI upper < 0) fired on
the 2025 holdout (-0.0021) and failed a stat whose primary contrast was -0.0116 [-0.0159, -0.0076] (T473). The diagnostic
(reports/shuffle_guard_diagnostic_2026-10-12.md) showed the guard fires in 9 of 48 honest shuffles (19%, plus ~10% the other way): the
spread across permutation seeds (sd ~0.0023) is as large as the bootstrap half-width, which ignores the fit noise of two models trained on
noise. Rule for future pre-registrations: size any shuffle or noise guard from a permutation null measured on the select season before the
freeze (K >= 50; fail beyond about 2 null sd), report the null sd next to the bootstrap CI, and prefer a noise-column control under real labels.

## Audit data capture before failing a gate (Devin, 2026-10-12)
Incident: on 2026-10-12 six draft rules were stopped at the minimum-data gate on 2023-24 alone, while 2019-21 history (data/history),
2022, preseason box scores and repairable sources were never considered. Rule: a gate that fails for lack of data is not final until a
data-capture audit is written into the draft: which seasons and sources were used, which exist but were not used (and why), which could
be fetched or repaired (cost, leak risk), and the gate re-measured on the fullest admissible data. Era differences (2019-20 bubble,
2020-21 COVID schedule and empty arenas) are flagged as features or excluded, never papered over with invented rows. Power is still set by
the report season; more training rows do not rescue a report season that is too small.

## Check a new rule's lineage before freezing (2026-10-12)
Incident: SEQ_FULL_MIX_RT was frozen as a W_ALL sequence model without noticing it is essentially ENSEMBLE E3, which an earlier
pre-registered guard (era-embedding probe, T443) had ruled ineligible; the new rule silently lacked that guard. Rule: before any freeze,
search the ledger for ancestors of the construction (same model family, same training window or features) and list in the draft every
guard that ever disqualified an ancestor, with "carried forward" or "dropped because ..." stated before any result.

## An invalid test is not a null (Devin, 2026-10-12)
"If there's more, better, more complete data, and the test we did in the past wasn't done correctly, then we don't know if it was right,
especially if neural networks get better with data." Rule: a result from a test later found flawed (proxy tip times before the 2026-10-09
real-tip fix, the DNP/played-games bug era, inputs contaminated by a later-found leak such as opp_adjusted_ridge, continuous-vs-integer
support mismatch, a training window too short for the model class) is relabelled INVALID, not NULL, and cannot be cited against the idea.
It stays open until a valid retest. For data-hungry models, "data starvation" is tested directly with a learning curve on the select
seasons (train on 1, 2, 3, ... seasons; a curve still rising at the largest window means the earlier loss may be starvation) before any
holdout is spent.

## A required pre-run review is a hard stop, not a citation (2026-10-12)
Incident: SEQ_FULL_MIX_RT's frozen Stage-2 step (3) required an adversary loader review after the freeze and before the Colab run; none was
done, the holdout row cited an older review, and a post-freeze notebook change (716da5e) went unreviewed (T479). The verdict survived only
because the post-result review found no defect. Rule: a scorer that touches a holdout refuses to run unless the review file the rule names is
committed after the freeze commit, says `Verdict: APPROVED`, and names the reviewed commit and export manifest that match what will run (a gate that checks only that a file exists opens on a BLOCKED review; found by the SEQ_LEARNING_CURVE loader review, 2026-10-12). A label shuffle is not a valid guard for a mixture contrast (a 50/50 mix wins on shuffled labels by spread
alone); use the run's permutation null and member-swap controls instead.

## A data change re-opens every gate built on it (2026-10-12)
Incident: the history players_static fill (for PPLAY_MINUTES_VALID) ran while ERROR_FEEDBACK's base, null and gate sat built on the old
store; its blind rehearsal caught the drift (regenerated base != stored) because pos_code had been 0 for every 2019-21-only player. Rule:
before changing a shared table, list the frozen or freeze-candidate rules whose gates read it; after the change, re-run their gates and
rehearsals before any freeze or run. Hash-bound identity checks (frame sha, regenerated-equals-stored) are what make drift visible; keep them.
An empty side table is a NULL pattern too: a feature that silently becomes 0 for a subset is the same survival leak as a NULL.

## Check what a command acts on before handing it to Devin (2026-10-13)
Incident: for SEQ_LEARNING_CURVE_2, two commands went to Devin checked only for existence. `history_static_fix fetch --apply` fetched
2019-21 players only, so the 2015-18 position gap stayed at 25.2% after he ran it; the download list left out the shotdetail archives the
loader needs (the only source of game_date), so the ingest stopped. Rule: before a command goes to Devin, read the code path it runs and
confirm its scope covers the target (seasons, ids, files), compare a download list against what the loader reads and what the last
download of that source held, and give the dry-run count where one exists. Devin's shell time is the scarce resource; one wasted run costs more than the check.

## A shadow that fails its own dry run says so where it is signed (2026-10-14)
Incident: COMBO_STOCKS_SHADOW was frozen while its 2024 dry run of the rule as written read only 4 of 7 targets passing; that fact sat
in a prior restatement, not in the line Devin signed. Rule: the signing line of any rule whose rehearsal or dry run shows a target
failing names those targets and their numbers.

## A report's time is the time printed in it, not the time in its file name (2026-10-15)
Incident: official injury-report PDFs named with an hour only (`..._06PM.pdf`) print "06:30 PM" in their header; the ingest stamped
them with the file-name time, 30 minutes early (190 of 190 sampled; files named with minutes were correct). Under a tip-60 cutoff the
INJURY_TIMING repaired table used a report published after the cutoff in about two thirds of 2023-24 games, and the planted-future
check could not see it because planted rows carry correct stamps. Stored 2022-24 history has the same offset: 3.8% / 4.8% of games
use a report published after tip-60. Rule: known-at comes from the document's own printed time (or the fetch time, whichever is
later), and a preflight refuses any row whose stamp disagrees with its source header.

- **A per-player treatment is clustered by player-season, not only by game (2026-10-20).** PRESEASON_ROLE_PRIOR (T500) applied one blend factor per player across his first 10 games; the game-clustered CI treated those 10 rows as independent and "cleared" three 2024 cells that vanish when clustered by player (pts [−0.0759, +0.0122]), and 5 of 115 players carried 98.6% of the gain. Rule: when the arm's effect is constant within a player (or team), the pre-registered CI clusters by that unit as well as by game, the power simulation includes player-shared noise, and the rule reports the share of the gain carried by the top five units. Re-check F9b (per-player role prior) for the same issue.
