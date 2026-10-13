# SEQ_LEARNING_CURVE: how much does the W_ALL sequence model gain from each added training season? (pre-registration, descriptive, no holdout touch)

Status: **FROZEN 2026-10-12 19:40 CDT (clock read by `date`; a first freeze commit 3c6d998, hash 1081eeed, carried a guessed 19:31 and is superseded by this one, no result existing).** Devin confirmed the drafted defaults for open questions 1-5 ("do 1 adn 2 adn 3", 2026-10-12, answering Claude's request to confirm the four defaults). No fit, no Colab run, no number from any window had been produced or read at the freeze. Freeze evidence: the commit `prereg(SEQ_LEARNING_CURVE): <sha256>` plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/SEQ_LEARNING_CURVE.md | shasum -a 256` (first 8 hex into the results and every ledger row). Everything above `=== RESULTS BELOW ===` is the rule.
Season labels are `games.season` (2015 = 2015-16, ..., 2022 = 2022-23, 2023 = 2023-24 = the select season, 2025 = the frozen holdout). Id: `SEQ_LEARNING_CURVE`.
Advice or momentum: **follows advice** (Claude's recommendation in DECISIONS 2026-10-12 17:25 item 1; BEST_PRACTICES "An invalid test is not a null": data-hungry models get a learning curve on the select season before anything is concluded; Devin chose it).
Holdout: **none touched.** The export is capped at season 2023 in code; the gate script reads `season <= 2024` in SQL on `nba.duckdb` (read-only); 2024 and 2025 outcomes are never scored here. HOLDOUT_ACCESS_LOG needs no row (the archivist confirms).

## Question and hypothesis
SEQ_FULL_MIX_RT (T478) passed on 2025 with the W_ALL window (2019-20 on). Its own run #1 showed the window matters: W_ALL minus W22 = -0.0120 [-0.0145, -0.0097] on 2023-24 pts (n 27,619). Is the curve still falling past W_ALL when four more seasons (2015-16 .. 2018-19, played rows only, no reports, no DNP rows) are added?
H1 (the decision-relevant one): holding the construction fixed, the 2023-24 pts integer CRPS of the production-shaped blend AVG falls by at least 0.003 between the 4-season window (= W_ALL) and the 8-season window, and is still falling by at least 0.0015 over the last two added seasons. Null: it is flat or worse past W_ALL. The follow-up is cheap (a draft); a wrong closure is costly, so the trigger is demanding on two points (size and still-falling) but the replicate guard is only a direction-and-half-floor check.

## Windows (exact seasons, training labels)
Window N holds the N seasons ending 2022-23 as training rows; it is scored on 2023-24 with the SEQ_FULL_MIX_RT walk (cold fit, then a warm refit before each calendar month of 2023-24 on every earlier row of the window plus earlier 2023-24 months). Same test rows, same blend, same comparators for every N.

| N | labels (games.season) | seasons | segments feeding it | note |
|---|---|---|---|---|
| 1 | 2022 | 2022-23 | S3 | = the W22 diagnostic of run #1 |
| 2 | 2021-2022 | 2021-22, 2022-23 | S2, S3 | 2021-22 report tokens dropped (RT rule) |
| 3 | 2020-2022 | 2020-21 .. 2022-23 | S2, S3 | |
| 4 | 2019-2022 | 2019-20 .. 2022-23 | S2, S3 | = **W_ALL** (reproduction anchor) |
| 5 | 2018-2022 | adds 2018-19 | S1, S2, S3 | first derived-box season |
| 6 | 2017-2022 | adds 2017-18 | S1.. | |
| 7 | 2016-2022 | adds 2016-17 | S1.. | |
| 8 | 2015-2022 | adds 2015-16 | S1.. | largest window the data we hold allows |
| 9, 10 | 2014-2022, 2013-2022 | not run | none | **no rows in either DB**; see the gate and open question 1 |

Segments (a token chain never crosses a segment boundary, as W_ALL's "separate export per era"): **S1** = labels 2015-2018 (history DB, play-by-play-derived box, `box_derived`), **S2** = 2019-2021 (history DB, official box and reports), **S3** = 2022-2023 only (`nba.duckdb`, capped at 2023; the 2024 rows are not exported). A window is defined by its **training target rows**; a row's input tokens are its last 20 team games inside its own segment (so an early-2020 row may carry 2019 tokens even in N = 3). Test-row inputs come from the single S3 export and are identical for every N. The cold-fit rows are the window's rows with `n_prior >= 3` dated before 2023-04-01 for 2022, early-stopping validation = 2022 rows from 2023-04-01 on (the same validation set for every N), warm pool = every window row with `n_prior >= 3` dated strictly before the month (the job asserts `max(pool date) < min(block date)`).

## How S1 (2015-2018) enters: decided now, never imputed
* **Rows:** played rows only. The 129-167 non-playing rows a season in the derived box (players with an event but 0 minutes) are dropped so that S1 absence tokens come only from gap games, uniformly. S1 has **no DNP rows**; the DNP-rotation teammate context is therefore zero in S1 and is flagged by the S1 channel, not filled.
* **Report tokens:** none. `has_report = 0` for every S1 row (no snapshot is loaded for 2015-18, including the 846 post-2018-12-20 games of 2018-19, where reports exist upstream but are not loaded; see repairs). Masked token plus the S1 era channel.
* **Position channels:** set to zero for **every** S1 row. Reason (new finding from the gate): the position cache holds only players still in the league at the 2025-26 pull, so position is missing for 41.6% / 32.3% / 22.6% / 11.0% of 2015-16 .. 2018-19 rows, a missingness that encodes survival to 2025, a future-derived NULL pattern. Zeroing it for all S1 rows removes the pattern inside S1 (S2 keeps W_ALL's behavior, 0.4% unknown; S3 0.0%).
* **Derived-box noise** (disclosed, not corrected): the 218-game independent sample shows 0 missing or extra played rows, every stat exact except pts and ftm off by 1 in 16 of 4,662 player-games (0.34%; 0.65% in 2015-16 and 2016-17 samples), and minutes within tolerance for 97.8% / 99.7% / 100% / 99.8% (2015..2018). The first-100-games normalizer prior (below) and this noise are the reasons S1 gets its own era channel.

## Era channels and rule-change flags (every export has all of them, so all windows share one input width)
Existing from RT (unchanged): `era_bubble`, `era_nofans`, `era_limited` (game_era_flags).
New, all **cumulative step indicators** (1 from the season in which the change took effect through the end of the data; 0 before), so the 2023-24 test rows share the vector of 2022-23 and no channel is ever 1 only on unseen test rows:

| channel | = 1 from | change | source (checked 2026-10-12) |
|---|---|---|---|
| `rule_away_foul_2016` | 2016-17 | one FT and possession for deliberate away-from-play fouls extended to the last 2 minutes of every period; pre-inbound fouls; back-jump presumptive flagrant | pr.nba.com/nba-board-governors-new-rules-away-from-the-play-fouls (release 2016-07-12, "2016-17 season") |
| `rule_timeouts_2017` | 2017-18 | 7 timeouts per team (18 to 14 per game), 75 s all timeouts, under-9 mandatory timeouts removed, 2 per overtime, FT-shooter delay-of-game, 15-min halftime | pr.nba.com/nba-rules-changes-2017-18 (release 2017-07-12, "2017-18") |
| `rule_reset14_2018` | 2018-19 | shot clock resets to 14 s after offensive-rebound situations | nba.com/news/nba-board-governors-approves-rule-changes; given as verified in the task; the search confirmed September 2018 approval, "ahead of the season" |
| `rule_nonbball_2021` | 2021-22 | interpretation change on non-basketball moves to draw fouls (an officiating emphasis, not a Board rule) | nba.com/news/amid-officiating-changes-free-throws-down-for-some-stars and trade coverage; given as verified in the task; the exact release was not re-fetched here |
| `rule_takefoul_2022` | 2022-23 | transition take foul: one FT (any player on the floor) and possession; not in the last 2 min of Q4/OT | nba.com/news/nba-board-of-governors-approves-heightened-penalty-for-transition-take-foul (2022-07-13, "2022-23") |
| `era_s1_derived_noreport` | S1 rows (2015-2018) | derived box, no DNP rows, no report snapshot, position zeroed | game_era_flags.box_derived / no_injury_report; DECISIONS 18:30 |
| `era_report_dropped` | 2021-22 rows | report tokens dropped by the RT rule; separates the two meanings of `has_report = 0` that RT disclosed | RT rule item 4 |

**Not used as flags (and why):** the 2023-24 Player Participation Policy (approved by the Board of Governors; release dated 2023-09-13: "will take effect with the start of the 2023-24 regular season", pr.nba.com/nba-board-of-governors-approves-player-participation-policy) and the 65-game / 20-minute awards threshold (part of the 2023 CBA; nba.com 2023-10-03 "Players share thoughts on new participation policy" calls it new under the new CBA; an official page naming its effective date was **not found**, first applied to 2023-24 awards per secondary coverage; treat the date as unverified) both begin in the **test season**. A flag that is 0 on all training rows and 1 on test rows cannot be learned, so neither is a channel. They are a disclosed **test-season regime shift** (load management and star availability change in 2023-24); the curve is measured across it, as T478's 2025 pass was. Also unverified and unused: any 2019-20 coach's-challenge flag; nothing else is added.
What the channels are: jointly they identify each training season (a season marker, exactly as RT disclosed for `era_limited`); that is flagging, not imputation. **Run-time gate:** on 100% of 2023-24 test rows the era vector equals the 2022-23 vector (all `rule_*` = 1, the three covid channels, S1 and report-dropped channels = 0); a mismatch voids the run.

## Stat normalizer (past-only, one definition for every season)
Reference rates L_ref = 2022-23 full-season mean per team-game (known before any 2023-24 game): pts 114.330, reb 43.422, ast 25.211, fg3m 12.315 (`reports/prereg_seq_learning_curve/gate.json`, frozen constants).
For a game on date d in season s, stat k: **L_s,k(d) = (sum of team-game totals of stat k over team-games in season s dated strictly before d + 200 * Lprev_s,k) / (number of such team-games + 200)**, and the factor is **c_k(d) = L_ref,k / L_s,k(d)**. Lprev_s,k is the full-season team-game mean of season s-1, a frozen constant from the table below (it is a completed season, so it was knowable before any game of s); for 2015-16 (no prior season in the data) Lprev = L_ref, so the first ~100 games of 2015-16 are shrunk toward 2022-23 rates (disclosed: that is the one place the normalizer is crude).

| season | pts | reb | ast | fg3m | | season | pts | reb | ast | fg3m |
|---|---|---|---|---|---|---|---|---|---|---|
| 2015-16 | 102.489 | 43.707 | 22.132 | 8.558 | | 2020-21 | 111.971 | 44.257 | 24.598 | 12.678 |
| 2016-17 | 105.621 | 43.409 | 22.598 | 9.705 | | 2021-22 | 110.322 | 44.287 | 24.524 | 12.422 |
| 2017-18 | 106.215 | 43.474 | 23.147 | 10.492 | | 2022-23 | 114.330 | 43.422 | 25.211 | 12.315 |
| 2018-19 | 110.989 | 45.155 | 24.488 | 11.363 | | 2023-24 | not used (test season; only its past-only running mean) | | | |
| 2019-20 | 111.646 | 44.710 | 24.276 | 12.275 | | | | | | |

Applied at the player-game table, before everything downstream: every token's pts/reb/ast/fg3m, every `base_*` recency mean (EW means are computed on the normalized series) and every training target are **multiplied by c_k(d of that game)**. The model predicts in normalized units; **at scoring the predicted mean and quantiles are divided by c_k(d_target)** (past-only at the target date) before the integer-grid conversion, so every arm is scored on the raw integer grid. Not normalized (scope fixed): minutes, starter flags, usage and zone shares (already ratios), team pace and rating (constants-based), Elo. Rationale for this scope: league rates of the four target stats are the quantities the rule changes and the 3-point era move; anything wider is a new model.
**Leak safety:** L(d) uses only rows dated strictly before d (same-day games excluded) plus a completed prior season. Using the target season's full-season rate would put April games into October inputs; per-season-full is therefore not used, even for completed training seasons, so the same definition holds for tokens, warm-refit labels and test rows. Tests (written after freeze, run before any fit): planted future (changing box rows dated >= d leaves c(d) bit-identical), c equals 1 for 2022-23-level inputs, a unit round-trip (normalize then divide returns raw to 1e-6), and test-row inputs bit-identical across windows.
Normalization is a **change from W_ALL** (which used fixed constant rescales only), so the run carries RAW anchors (below) and a pre-declared fallback.

## Arms, seeds, compute
* **NORM(N), N = 1..8:** the RT E2 program unchanged (hidden 64, 3 seeds [0, 1, 2], cold: 25 epochs max, patience 4, lr 1e-3, batch 1024; warm: 3 epochs, lr 3e-4 before every calendar month of 2023-24; real-tip report tokens for S2 2019-20/2020-21 and S3, none for S1 and 2021-22) with the channels and normalizer above. One policy for all N: **same epochs cap, patience, learning rates, batch and seeds; no window gets tuned.** Gradient steps grow with N (epochs are fixed, as in W_ALL); each window reports the best epoch per seed. If more than one third of cold fits reach the 25-epoch cap, the verdict is **INVALID (budget)** and a new id reruns with a higher cap: a window that is still improving when stopped would manufacture "still rising".
* **RAW(N), N = 1, 4, 8:** exactly the RT construction (no rule/S1 channels beyond the 3 RT era channels, fixed-constant scaling), S1 enters N = 8 with `has_report = 0`, position zeroed for S1 rows, no normalization. RAW(1) and RAW(4) are the reproduction anchors for run #1's W22 and WALL; RAW(8) isolates what normalization and flags do at the largest window.
* **Replicate ensembles, NORM N = 4, 6, 8:** a second independent 3-seed set (seeds 3, 4, 5), same everything. They estimate training-seed noise, which the game-clustered bootstrap does not cover.
* Comparators and blend: A0 and FULL are the stored 2023-24 integer grids (T467 identity as in RT gate (i)); SEQc(N) = T442 learned blend of [A0, SEQ(N)] on regular-season rows (postseason and play-in rows take A0), run over the 2023-24 rows in date order (30-day blocks, 3000-row minimum, strictly earlier rows, 0.5/0.5 before the first fit); **AVG(N) = 0.5 pmf(SEQc(N)) + 0.5 pmf(FULL)** on the integer grid.
* **Compute (estimate, not measured):** about 67 season-units of training (NORM 36, RAW 13, replicates 18); run #1 took 22 T4 minutes for about 9 units over two test seasons, so this job is about **60-150 T4 minutes** (one test season, 9 refits per window). Ceiling: stop at **240 T4 minutes** and report what completed; never cut an arm silently (an unfinished arm is reported as unfinished and the verdict is INCONCLUSIVE). Per-window checkpoints: each `oof_<arm>_<N>.npz` is written on completion and the job skips finished windows on restart; progress is printed per window and per block. `configs/cost.yaml` units: last read 75 (2026-10-09), per-hour rate unread (open question 4). Mac: the three exports (S1, S2, S3) and scoring, about 10-20 minutes, under `data/ops/heavy.lock`, nothing fanned out.
* **Code and notebook (to be written after the freeze, under this rule):** `research/features/seq_learning_curve_export.py`, `research/colab/jobs/seq_learning_curve/` (job.yaml, `build_notebook.py`, `seq_learning_curve_train.py`), `research/eval/seq_learning_curve_eval.py`. Notebook: `research/colab/jobs/seq_learning_curve/seq_learning_curve.ipynb`, staged at `data/colab/rclone_stage/nba_colab/seq_learning_curve/<run_id>/seq_learning_curve__<run_id>.ipynb` (the clickable link is given when it is built). T4, Python 3, Run All.

## Metric, criterion and verdicts (written before any window is run)
* **Metric:** pts integer-grid CRPS, 2023-24, every row scored by the walk (the same rows for every window; the job asserts identical row sets). Notation: C(N) = CRPS of AVG(N). **Paired game-clustered bootstrap:** one set of 2000 resamples of `game_id` (seed 0) is drawn once and every contrast is computed on the same draws; 95% percentile CI.
* **A = C(8) - C(4)** (what the four derived-box seasons add past W_ALL) and **B = C(8) - C(6)** (what the last two added seasons, 2015-16 and 2016-17, add). Negative is better. Practical floors: **A <= -0.003** (the minimum detectable effect at n about 27,600 rows, per RT) and **B <= -0.0015** (half of it, over two seasons, i.e. still falling at the end).
* **RISING** (triggers the follow-up draft) requires all of: (i) A <= -0.003 with CI upper < 0; (ii) B <= -0.0015 with CI upper < 0; (iii) replicate guard: the replicate ensembles give A_rep <= -0.0015 and B_rep < 0 (same signs, at least half the floor); (iv) the reproduction and run-time gates pass; (v) the budget diagnostic passes. This is an intersection of conditions, so no multiplicity correction is applied to it (m = 1 decision).
* **NOT RISING (closes the question):** (i) or (ii) fails with the gates passing. Flat past W_ALL is a finding; the window stays W_ALL. **HURTS:** A >= +0.0015 with CI lower > 0; this is the reversal trigger DECISIONS 17:41 named for 2015-18 ("the learning curve showing 2015-18 seasons hurt after era normalization"), reported to Devin, whose decision it is.
* **Fallback (declared now, not chosen after seeing):** if NORM(4) - RAW(4) >= +0.003 with CI lower > 0 (the new channels/normalizer hurt the W_ALL baseline), the verdict is **INCONCLUSIVE (construction)**; A_raw = C_raw(8) - C_raw(4) is reported, no follow-up is triggered from it, and the next step is Devin's.
* **Reproduction gates (before the curve is read; a failure voids the run and the harness is fixed, the rule untouched):** RAW(1) AVG - FULL on 2023-24 within 0.003 of run #1's W22 diagnostic (-0.0083), RAW(4) within 0.003 of run #1's WALL (-0.0203) (RT recorded +0.0017 / +0.0013 of rerun noise on this very comparison, hence 0.003), with the stored A0/FULL grids reproducing to 1e-6.
* **Multiplicity:** the decision family is m = 1. **Secondary family S (BH, m = 6, descriptive, cannot rescue or change a verdict):** A and B for reb, ast, fg3m (AVG - FULL scale, integer grid). Also reported with CIs and no family: every step C(N) - C(N-1), N = 2..8; SEQc(N) - A0 curves for the four stats; NORM(4) - RAW(4), NORM(8) - RAW(8), NORM(8) - NORM(4) against RAW(8) - RAW(4); the seed-noise sd from the replicates.
* **Slices (fixed now, descriptive):** month blocks (Oct-Nov, Dec-Jan, Feb-Apr, postseason), starters vs bench (as-of `starter10 >= 0.5`), cold start (`n_prior < 20`), realised minutes < 5, rows without a report token, with n and a clustered CI each. The W_ALL gain was largest early in the season and faded; the early/late split is how a pool-size effect would show here.
* **Calibration (descriptive):** pts coverage at q05..q95 of SEQc(N) for every N, so a CRPS "gain" from a calibration shift is visible. No guard in this rule can fail it: it is a curve, not a candidate.

## What happens next
* **RISING:** one pre-registered follow-up draft (new id `SEQ_LEARNING_CURVE_2`), no earlier: add 2013-14 and 2014-15 (repair below) and run windows 9 and 10 plus replicates; then closed. Any change of the production training window is Devin's decision and a new model-variant pre-registration (SEQ's one 2025 touch is spent on W_ALL; a W_8 or W_10 variant would be a different never-evaluated model).
* **NOT RISING:** closed; W_ALL stays the window; no pre-2015 data is pursued for this model.
* **Nothing here is a betting claim or a production proposal.** It says how much a window matters on one select season.

## Data gate (non-negotiable 8), checked 2026-10-12 against the real tables
Script: `.venv/bin/python -I reports/prereg_seq_learning_curve/gate_check.py` (read-only; history DB seasons 2015-2021 and `nba.duckdb` with `season <= 2024` in SQL); output `reports/prereg_seq_learning_curve/gate.json` and `gate_stdout.txt`. Thresholds below were chosen before looking at the per-season numbers they judge only where marked "set from the existing RT gates".

Per season (games / played rows / non-playing rows in table / games with report rows / games with possessions): 2015: 1,316 / 27,817 / 163 / 0 / 1,316; 2016: 1,309 / 27,709 / 167 / 0 / 1,309; 2017: 1,312 / 27,683 / 161 / 0 / 1,312; 2018: 1,312 / 27,739 / 129 / 0 / 1,312; 2019: 1,143 / 24,097 / 4,610 / 1,141 / 1,143; 2020: 1,171 / 25,053 / 6,493 / 1,171 / 1,171; 2021: 1,323 / 28,045 / 5,803 / 1,323 / 1,323; 2022: 1,320 / 27,737 / 6,287 / 1,318 / 1,320; 2023: 1,318 / 28,197 / 6,618 / 1,317 / 1,318; 2024 (not used here): 1,320 / 28,236 / 6,851 / 1,317 / 1,320.

| gate | threshold | result | verdict |
|---|---|---|---|
| G1 box rows for every game, every held season | games_with_box = games | 10 of 10 seasons equal | PASS |
| G2 played rows per season | >= 24,000 | min 24,097 (2019-20, shortened); 27,683-28,236 elsewhere | PASS |
| G3 null pts/reb/ast/fg3m/starter/fga/tov among played rows | <= 0.1% | 0 nulls in all seasons | PASS |
| G4 exactly 5 starters per team-game | >= 99.5% | 100% in all seasons | PASS |
| G5 box minutes per game / 240 | 2.00-2.03 (OT inflates) | 2.011-2.015 in all seasons | PASS |
| G6 possessions for every game; shot possessions without a shooter | 100%; <= 0.5% | 100%; 0 | PASS |
| G7 era-flag row for every game | rows = games | equal for all seven history seasons and 2022-24 | PASS |
| G8 league-rate drift needs a normalizer | descriptive | pts/team-game 102.5 (2015) to 114.3 (2022); fg3m 8.6 to 12.3 (+44%); ast 22.1 to 25.2 | normalizer justified |
| G9 DNP rows exist (the W_ALL seasons have 4.6k-6.9k a season) | present | **S1: 129-167 a season** | **FAIL for S1 by construction**, not repairable here (cancelled 2026-10-12 18:44); the rule takes S1 as played-only + flag, so the rule does not need them |
| G10 injury-report tokens exist | present | S1: 0 games with report rows (2018-19: 466 games flagged `no_injury_report`, the other 846 post-12-20 games have no loaded rows) | **FAIL for S1**; rule masks them (has_report = 0 + S1 channel) |
| G11 real tips for report-token seasons | present | schedule parquet exists for 2019-20 onward; none for 2015-18 (not needed: no tokens) | PASS where needed |
| G12 position known for >= 99% of rows | >= 99% (set from RT's 0.4% unknown) | S1: 58.4% / 67.7% / 77.4% / 89.0% known by season (survivorship NULL pattern); S2 99.6%; S3 100% | **FAIL for S1**; rule zeroes position for every S1 row |
| G13 derived-box reconciles to official | pts/ftm > 1% off or minutes < 91% within tolerance fails (DECISIONS 18:30) | 218 games, 4,662 rows: 0 missing/extra, pts/ftm 0.34% off by 1, minutes 97.8-100% | PASS |
| G14 windows 9 and 10 exist | rows for 2013-14 and 2014-15 | **none in either DB** | **FAIL (data absent)**; windows 9-10 not in this rule |

**Repairable gaps, with cost, before calling anything failed:**
1. *Windows 9 and 10 (2013-14, 2014-15).* The shufinskiy/nba_data source (Apache-2.0) covers 1996-97 through 2024-25; our download (`data/raw/nba_data_shufinskiy_2026-10-12/`) holds only nbastats 2015-2020 and the playoff files. Cost: Devin downloads `nbastats_2013`, `nbastats_2014` and the `_po_` files (about 16 MB, free, command in open question 1), then one archivist run through the existing adapter `nba/ingest/nbastats_pbp.py` plus the 218-game-style reconciliation for the two seasons and a rerun of this gate. Recommended default: **not now**, because the follow-up draft is the designed place for it and only if the curve is still rising at 8 seasons.
2. *DNP rows for S1.* Cancelled by Devin (18:44); cost was ~9 h of stats.nba.com queue for 5,249 games. Not needed by this rule.
3. *2018-19 reports for the 846 post-2018-12-20 games.* The official PDFs exist upstream; archive starts between 2018-12-14 and 2019-04-05, not loaded. Cost: a fetch plus parse by the archivist (the 2019-21 parser exists), unknown days of PDFs. Not needed: S1 is masked whole, which also keeps the S1 window uniform.
4. *Position for 393 S1 players.* One commonplayerinfo call each (about 393 requests, queue-limited), or a free source; not needed because S1 position is zeroed.
No gate failure is on data this rule needs. G9, G10, G12 are failed-by-construction inputs the rule does not use; G14 removes windows 9-10 from this id.

## Playbook check (neural sequence model; docs/MODEL_PLAYBOOK.md)
1. **Information or capacity?** Capacity/data only: no new pre-tip information; it asks whether more history, not more features, still helps, which the playbook says is the one place neural models can differ from trees (data appetite high). Informative either way, and we expect small effects.
2. **Enough data for this class?** This rule is the learning curve the playbook demands. The largest window we hold is 8 seasons (about 190k trainable rows); windows 9-10 do not exist (G14).
3. **Test valid?** Real-tip report tokens carried from RT (S1 has none); integer-grid scoring for every arm; same rows, same blend, same comparators, same early-stopping validation set; game-clustered paired CI; reproduction anchors; seed-noise replicates (the bootstrap alone ignores training noise); budget-cap diagnostic (a stopped-early large window would fake a flat or rising curve). Normalization is a change from W_ALL, so RAW anchors and a pre-declared fallback exist.
4. **Lineage and inherited guards.** Essentially ENSEMBLE E2 -> E3 (T441-T443) -> SEQ_FULL_MIX_RT's W_ALL. Guards that disqualified ancestors: (a) proxy-19:00 tip leak (T441-T445 invalid): carried, real tips only, S1 has no tokens; (b) E3's embedding era probe (AUC 0.998, T443): **dropped as a verdict gate** because this rule promotes nothing, replaced by the run-time gate that the test-row era vector equals 2022-23's; the probe may be run descriptively; (c) T442's calibration (5% quantile covers 7.6%) and slices (minutes < 5, postseason): carried as descriptive slices and a coverage table, not pass criteria; (d) the 2021-22 hourly-report season: carried (tokens dropped, now with its own channel); (e) the missingness leak fingerprint: audited above (position survivorship in S1, handled by zeroing; DNP-context zeros flagged by the S1 channel).
5. **Scored against:** CRPS of the whole distribution of the AVG blend versus FULL on pts, not a line-level log loss; no market comparison is made or implied (T477: CRPS gains have not reached the line).
Class checks: training window long enough? that is the question. Era channels zero on test rows? replaced by identical-to-2022-23 (cumulative steps), gated at run time. Lineage guards carried? listed in item 4.

## What each choice protects against (one line each)
* Windows defined by training rows, test inputs from one export: a window cannot change the test inputs, so differences are the training data only.
* S1 played-only, masked reports, zeroed position, own channel: no imputed DNP/report/position values and no survivorship NULL pattern (the leak class that slipped through three experiments).
* Cumulative step rule flags, none for the 2023-24 policies: no channel that is 1 only on test rows (unlearnable) and no era shortcut that carries test information.
* Past-only league-rate normalizer: no October input depends on April games; the same definition for tokens, labels and test rows.
* RAW anchors + fallback: a construction change cannot be mistaken for a window effect.
* Replicate ensembles: training-seed noise (about 0.001-0.002 in RT's rerun) cannot masquerade as a rising curve.
* Epoch-cap diagnostic: a window stopped before convergence cannot produce a false flat or rising curve.
* Intersection criterion with a size floor and an end-of-curve floor: a gain concentrated in early windows, or noise at the last step, does not trigger the follow-up.
* One select season, no 2024/2025: no holdout is touched, and nothing is selected on the report season.
* Scope: nothing is promoted; a new question (production window, other stats' windows, windows 9-10) is a new id.

## Honest prior, predictions, forking paths
* **Everything was chosen before any window was run.** Seen: RT run #1's W22/WALL figures (2023-24: -0.0083, -0.0203; history step -0.0120 [-0.0145, -0.0097] / -0.0072), T478, the gate tables above. Never seen: any NORM or RAW window, S1 sequence export, any 2024/2025 SEQ output from this construction beyond T478.
* **Predictions (stated now, for docs/PREDICTIONS_LEDGER.md):** P(RISING by the full criterion) = **0.12**. P(A <= -0.003 with CI upper < 0) = 0.38. Expected A = **-0.0025** (80% interval -0.007 to +0.002); expected B = **-0.0006** (-0.0025 to +0.001). P(HURTS) = 0.10. P(curve falls from N = 1 to N = 4 by at least 0.008) = 0.90 (RT measured -0.0120). P(reproduction gates pass) = 0.85. P(epoch-cap diagnostic fails) = 0.10. Reasoning: the 4-season step (-0.0120, about -0.004 a season) already decays within W_ALL (2023 to 2024: -0.0120 to -0.0072); the next four seasons are further away (the 3-point rate rose 44% from 2015-16 to 2022-23, pace and officiating changed), lack DNP and report context, carry derived-box noise, and are mixed 50/50 with FULL, which dilutes any member gain; normalization and flags should recover part but not all of it.
* Forking paths: floor sizes (0.003 / 0.0015) set from RT's MDE, not from a curve; windows 4/6/8 as the contrast points; the choice to normalize and to keep one flag set for all windows; the reference season for the normalizer (2022-23).

## Kill criteria
* Any run-time or reproduction gate fails: nothing is read; the harness is fixed and rerun under this id (the rule is unchanged) or, if the rule itself is at fault, withdrawn and redrafted with a new id before any curve is seen.
* Any 2024-25 or 2025-26 outcome scored or loaded for this rule (the exports are capped at season 2023): void.
* A window not completed within the 240 T4-minute ceiling: INCONCLUSIVE; no partial curve is called.
* Not RISING: closed; no variant windows, floors, normalizers or seeds are tried afterward (at most the one pre-registered follow-up after RISING).

## Before the run
* An adversary loader review of the export, the notebook and the scorer, committed as `docs/reviews/seq_learning_curve_loader_<date>.md` after the freeze commit, before any Colab run. The run script refuses to start without it (BEST_PRACTICES 2026-10-12, "A required pre-run review is a hard stop").

## Open questions for Devin (answered 2026-10-12: every bracketed default confirmed)
1. Windows 9-10: download 2013-14 and 2014-15 now so one run covers 1..10? [No: windows 1..8 now; 9-10 only in the follow-up if RISING. If yes: `mkdir -p data/raw/nba_data_shufinskiy_2026-10-12_b && cd` there and fetch `nbastats_2013.tar.xz`, `nbastats_2014.tar.xz`, `nbastats_po_2013.tar.xz`, `nbastats_po_2014.tar.xz` from the shufinskiy/nba_data release assets; then ingest, reconcile, rerun the gate, and add N = 9, 10 before the freeze.]
2. Confirm the S1 decisions: played rows only, no report tokens, position zeroed for all S1 rows. [Yes.]
3. Confirm the floors (A -0.003, B -0.0015) and the replicate guard (direction plus half the floor). [Yes.]
4. Colab budget: about 60-150 T4 minutes (estimate); ceiling 240. `configs/cost.yaml` rate unread. [Run if units allow; otherwise defer.]
5. NEURAL_FAMILY_WALL (uncommitted draft) trains C1/C2 on W_ALL; should it wait for this curve's verdict? [No wait needed; W_ALL stays the window unless this rule says RISING.]

## Data exposure and unverifieds
Read here: the two DBs (counts, means, flags; read-only, `season <= 2024`), RT's rule and run #1 figures, the box-sample reconciliation report, the web pages cited. **Could not verify:** an official page for the 65-game threshold's effective date; the exact 2021-22 release (relied on the task's statement plus trade coverage); the sha of the S1 export (not built); compute minutes (estimated from run #1, not measured); `configs/cost.yaml` per-hour rate.

=== RESULTS BELOW ===

(not run)
