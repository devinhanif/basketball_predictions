# Retrain survey, 2026-11-17 (modeler, read-only; nothing fitted, nothing installed)

Asked by Devin on 2026-11-17: "it's mid November so we need to do the retraining asap" and "make sure the model is calibrated always".
This is **advice work**. It follows the 11-08 hand-off (NEXT_SESSION 11-08 item 2, PROJECT_STATUS section 7 item 2), which says the AVG November refit is overdue.
Written 2026-11-17 at about 19:00 CST from `main` at `04763a5b`. Sources: the repo docs, the file artifacts under `data/`, and a scratch copy of
`data/export/latest/export.duckdb`, which the export manifest stamps 2026-11-18T00:35Z, data_version `6edbcef0c89d`; every query used `season = 2026`.
No DuckDB file under the main checkout was opened. No season-2025 row was read.

**Bottom line.** One retrain is due: the **AVG shadow's November weights**. That refit is a 3-6 minute Colab job, and it is already staged correctly; it
does not need restaging. Everything else the system serves either refits every day inside the launchd jobs (data through 2026-11-16 on tonight's
rows) or is frozen by its rule and is not due. There is also bad news the refit cannot fix. AVG has been stale for 17 days, so its frozen
forward test (AVG_SHADOW_LIVE_2) will be **INCONCLUSIVE at the 2027-01-10 look whatever happens now**: about 22% of the games to that date are stale, and the rule's limit is 10%.
The refit still matters for two reasons. It puts the AVG model back on the app (tonight 548 of 596 shown prop rows fell back to production
as `fallback_stale`). And it keeps the season-end second look barely alive: if the weights are installed before the first 11-18 game and every later month is on time, about 10% of games are stale.

"Calibrated always" cannot be met today by refitting anything. Most model probabilities on screen have no calibrated number. Tonight only 65 of 596 prop-stats got
a calibrated main-line chance, and 122 of 1,025 Kalshi rungs. The causes are mostly coverage and pairing, not missing models. The one
calibration map we have is frozen and under a live test until its look (about 12-01; probably later, because of the 11-14/15 odds outage).

---

## Part 1: every fitted artifact the system serves, and what is due

"Daily" means the pretip or lineups job refits it on rows dated before the slate date. Those need nothing from anyone. Dates were checked in the export
`model_stamps` and `prop_grids.stamp_fit_through` for the 11-17 slate, and in the cache directories under `data/models/`.

| # | Artifact (class) | Where it lives | What refits it, by its own rule | Last fit / fit through | Stale today? | Who |
|---|---|---|---|---|---|---|
| 1 | Game winner `rung0_injury_elo` v2: MOV-Elo plus ridge (b, g) (ratings + linear) | nothing stored; `configs/injury_elo.yaml` | refit from scratch on every run, completed games < slate date (DAILY_PIPELINE section 4) | fit_through 2026-11-16 (stamps, 186 rows) | no | daily job |
| 2 | `rung0_mov_elo` comparator (ratings) | `configs/mov_elo_tuned.yaml` (params frozen) | ratings refit every run | 2026-11-16 | no | daily job |
| 3 | Production props `props_context_residual` v2: LightGBM mean + scale + conformal (GBM) | `data/models/context_residual/<date>/models.pkl` | daily, played rows < slate date | cache 2026-11-17, fit_through 2026-11-16 | no | daily job |
| 4 | `_int` (integer support) and `_lt` (pts short-minutes mixture `pi`) arms | `.../<date>_lt` | daily, same rule | 2026-11-16 | no | daily job |
| 5 | `_t30`, `_t15`, `_late` arms | `data/models/context_residual_t30/<date>`, `context_residual_late/{ctx,t30}/<date>` | daily | 2026-11-17 caches | no | lineups tick |
| 6 | Minutes hurdle / production `p_play` (GBM) | inside the production fit; Platt map OFF since 10-11 (`nba/props/forward.py`) | daily | 2026-11-16 | no | daily job |
| 7 | P(play) shadow R `pplay_r_v1` (GBM) | refit each run | daily | `r_train_through` 2026-11-16, n_train 134,221 | no | daily job |
| 8 | FULL shadow (GBM, recent-role) | `.../<date>_full` | daily | 2026-11-17 cache | no | daily job |
| 9 | COMBO shadow, blk/stl NB heads (count) | `.../<date>_combo`; R and base normals frozen by rule (`configs/combo_stocks_shadow.yaml` 8932ccec, normals 08ddb2a2) | NB daily; R never | 2026-11-17 cache | no | daily job |
| 10 | **AVG shadow E2 sequence weights (neural sequence)** | `data/avg_shadow/bundles/<YYYY-MM>/seed0-2.pt` | **monthly**: warm block on every row dated before the 1st, installed by 12:00 CT on the 1st (AVG_SHADOW_LIVE_2 "What the steward builds" 3; integrity "Refit staleness") | bundle 2026-10 `b8d2b27b`, fit_through 2026-06-13 | **YES, since 11-01 (17 days).** Every row is `stale_model=1`; the watchdog has alerted every 6 h | **Devin (Colab) + Claude (assemble, install)** |
| 11 | AVG mixing gate (T442 SoftmaxGate) | `gate.json` in the bundle (`58043d34`, gate_fit_through 2026-06-13) | rule text: monthly, with the shadow's own stored SEQ/A0 forecasts added. **November: carry the October gate** (DECISIONS 2026-11-08 11:15, Devin, disclosed deviation) | 2026-06-13 | not due for November (approved deviation); **December follows the rule text unless a new DECISIONS row says otherwise** | Claude locally (Mac, CPU), after the work in Part 2.4 |
| 12 | LINE_CALIBRATION map (logistic recalibration) | `nba/parlay/line_cal_map_v1.json` (payload 443743a1; DK + Pinnacle x 4 stats) | static: "Refit only after Devin logs a 2025 training-use row"; LINE_CAL_LIVE section 2: "No refit, ever, inside this rule" | fit_through 2025-06-30 (2023-24 legs) | not due; frozen until the LINE_CAL_LIVE look | a new rule id after the look |
| 13 | ALT_RUNG_CAL map (logistic) | `nba/parlay/rung_cal_map_v1.json` (116f4480, DK only) | static, same note | 2025-06-30 | not due | new rule id |
| 14 | Combo params (copula correlations, script shifts) | `nba/parlay/combo_params_v1.json` (8b5a124e) | static; the forward script rule is still a DRAFT | 2023-24 | not due | n/a |
| 15 | Parlay joint model (game model + pair correlations) | `data/parlay/joint_model.json` (2026-10-08; 2022-24 / 2023-24) | `python -m nba.parlay fit` by hand; **no cadence rule** | 2026-10-08 | not due under any rule | a refit is a model change (needs a prereg) |
| 16 | Parlay market-prior weight (`market_prior.k` 100, skill gate) | computed on every `evaluate` from the paper track record | every run | weight 0 since 11-02 | no | daily job |
| 17 | JARVIS per-stat model weights | `data/jarvis/model_weights.jsonl` | weekly, automatic in the JARVIS tick (frozen rule in `configs/jarvis.yaml`) | 2026-11-16: every stat holds at 0 (n 0-7 per stat, far below min_n 30) | no; next about 11-23 | JARVIS tick |
| 18 | Halftime in-game line model: T508 arm M + glm_e + Platt (GBM + logistic) | `data/models/halftime_ingame/2026-10-27/artifact.json` (digest b9b53d63, pinned in `configs/jarvis.yaml`) | fit once on 2019-2024; no cadence (HALFTIME_OVERS_VS_PRICE is a draft) | 2026-10-27 | not due | n/a |
| 19 | P(play) Platt pair (-0.327, 0.581) | constant in `nba/props/forward.py` | OFF since 10-11 (it hurt log loss) | n/a | n/a | n/a |
| 20 | Q1 recalibration | `configs/q1_recal.json` (2024) | read only by `nba/eval/q1_starters_live.py` (a draft rule); not served | n/a | n/a | n/a |

No fitted artifact: TEAMMATE_SITS reruns production's own model; the LLM shadow is off.

**Refit every day by the launchd jobs, so they need nothing:** rows 1-9, 16 and 17. Production props have already "retrained" through last night: the
LightGBM heads refit each morning on every played row before the slate. Retraining them more often or by hand would add nothing, because the
weights already follow the data. **Due now: row 10 only.** **Due 2026-12-01:** row 10 again (the December block), plus row 11 if the rule text is followed.

---

## Part 2: staging what is due

### 2.1 Is the 11-08 staging what the rule asks for? Yes; do not restage

- **Data cut.** The rule wants a "warm refit on every row dated strictly before the month". It also says a row is valid only if `fit_through` is before the first day of
  the game's month. The staged export `data/colab/avg_shadow_monthly/2026-11/export_info_live.json` has `before_date` 2026-11-01,
  `max_date` 2026-10-31, 1,990 played rows over 88 games, 0 games without a real tip, and data_version `22566ef2b04a`. The schedule
  (`data/schedule/live_tips_2026-27.parquet`) has exactly **88 regular-season games from 10-20 to 10-31**, so October is complete.
  **Restaging with data through mid-November would be wrong.** It would set `fit_through` on or after 11-01, and then *every* November row would stay invalid
  under the rule (`select_bundle` would keep `stale_model = 1`).
- **Files.** The hashes I read tonight: rule `AVG_SHADOW_LIVE_2.md` (the staged copy is byte-identical to main's); live npz `65df0544` (matches the info file); the cur25 and hist exports
  `316d8e1e` / `1b30eef0` (match the October manifest); `prev_bundle/` = October `b8d2b27b` (all four file shas match). The staged
  `HOLDOUT_ACCESS_LOG.md` has the 2026-11 monthly-refit row, which is on main as row 188 (commit e9d501e). Folder size is 73 MB.
- **Code after today's merge.** The job lives only on the unmerged branch `agent/steward-avg-monthly-refit` (`5a11b035`, worktree
  `/Users/devin/Downloads/nba-wt-avgrefit`). Since that branch's base, main has changed in AVG-relevant code only by `display.py` (an identical-value
  constant import) and a comment in `configs/cost.yaml`; `bundle.py`, `infer.py`, `grids.py`, `sequences.py` and the rule are unchanged. I trial-merged the
  branch onto main `04763a5b` in a scratch worktree. Only `docs/DECISIONS.md` and `docs/HOLDOUT_ACCESS_LOG.md` conflicted, and both sides were the same rows that main
  already has. The AVG tests on that merge passed: `tests/daily/test_avg_install_check.py test_avg_shadow.py test_avg_shadow_monthly.py
  test_avg_shadow_rebuild.py test_gate_rows_alignment.py` gave **61 passed, exit 0**. The notebook in the worktree is byte-identical to the one on the merge (sha256
  `e0076404...`). **So the notebook is still the right one.**
- **One catch for the assemble step.** Main's `assemble_bundle.py` lacks the branch's monthly fields: it reads `parts["oof_sha256"]` unconditionally,
  and a monthly run has no OOF. Assemble from the `nba-wt-avgrefit` worktree (its `data/` is a symlink to the main checkout's `data/`). Install with main's
  `bundle.py`, which is unchanged.
- **Unverified (open question 1 of the runbook):** whether every October game had possessions loaded when the export ran on 11-08. Training and
  serving use the same live exporter, so this is a fidelity question, not a train/serve mismatch. It gates the install (installs are never overwritten), not the Colab run.
  A 5-second read-only count is in 2.3 step 2.

### 2.2 Devin's steps (tonight; about 10 minutes, of which 3-6 are T4)

1. Upload the staged folder (73 MB, 1-2 min):
   ```
   /opt/homebrew/bin/rclone copy /Users/devin/Downloads/nba-prediction/data/colab/avg_shadow_monthly/2026-11/run gdrive:nba_colab/avg_shadow_monthly/2026-11
   ```
2. Open and **Run All**: [avg_shadow_monthly.ipynb](/Users/devin/Downloads/nba-wt-avgrefit/research/colab/jobs/avg_shadow_monthly/avg_shadow_monthly.ipynb).
   In VS Code choose the Colab kernel, runtime **T4 GPU**, Python 3. The notebook expects about 2-4 minutes, warns past 20 and halts at 60. It mounts Drive, finds `nba_colab/avg_shadow_monthly/2026-11`,
   prints `staged run folder:` and the rule sha `9afbfeb6...`, and writes `artifacts/<stamp>/` there. Cost at 1.27 units per T4-hour is about 0.1 units.
   If the VS Code kernel cannot mount Drive, the fallback is to upload the same .ipynb in the Colab web UI (the browser `colab.research.google.com/github/...` link works only if Colab can see the repo).
3. Download the result (seconds):
   ```
   /opt/homebrew/bin/rclone copy gdrive:nba_colab/avg_shadow_monthly/2026-11/artifacts /Users/devin/Downloads/nba-prediction/data/colab/avg_shadow_monthly/2026-11/artifacts_drive
   ```
4. Tell Claude "done". **Every game day before the install adds that day's games to the stale count** (about 7 a day on average; 12 on 11-18; see 2.5). Finishing before tomorrow's first pretip
   (07:20 CT on the launchd UTC-4 clock) is the difference that matters.

### 2.3 What Claude (main session) does after "done"

1. Copy the newest `artifacts_drive/<stamp>/` files (`seed0.pt seed1.pt seed2.pt vocab.json bundle_parts.json config.json metrics.json`) to
   `data/colab/avg_shadow_monthly/2026-11/artifacts/`. Read `metrics.json` and `bundle_parts.json`: `smoke` must be false, `month_block` 2026-11,
   `before_date` 2026-11-01, `live_rows` 1990, `rule_sha256` 9afbfeb6..., `prev_model_id` b8d2b27b..., and runtime under 60 min.
2. **Possessions check, read-only, after tonight's last lineups tick (about 23:30 CT) or before 06:00 CT.** Run from the main checkout. Expect `(88, 88)`; anything else, STOP and do not install:
   ```
   uv run --no-sync python -c "import duckdb;c=duckdb.connect('nba.duckdb',read_only=True);print(c.execute(\"select count(distinct g.game_id), count(distinct p.game_id) from games g left join possessions p using(game_id) where g.season=2026 and g.game_id like '002%' and g.game_date<'2026-11-01'\").fetchone())"
   ```
3. Assemble with the October gate (DECISIONS 2026-11-08 11:15), from the worktree:
   ```
   cd /Users/devin/Downloads/nba-wt-avgrefit && cp data/avg_shadow/bundles/2026-10/gate.json data/colab/avg_shadow_monthly/2026-11/gate.json && PYTHONPATH=. .venv/bin/python research/colab/jobs/avg_shadow_rebuild/assemble_bundle.py --run data/colab/avg_shadow_monthly/2026-11/artifacts --gate data/colab/avg_shadow_monthly/2026-11/gate.json --gate-fit-through 2026-06-13 --out data/avg_shadow/incoming/2026-11
   ```
   Expect `month_block` 2026-11 and `fit_through` 2026-10-31. `manifest.json` carries `prev_model_id` b8d2b27b....
4. Install. This copies files only, with no database write, so it is safe at any hour. It is irreversible: a month's weights are never overwritten. Run it from the main checkout:
   ```
   cd /Users/devin/Downloads/nba-prediction && PYTHONPATH=. uv run --no-sync python -m nba.daily.avg_shadow.bundle install data/avg_shadow/incoming/2026-11
   ```
   Then run `cd /Users/devin/Downloads/nba-wt-avgrefit && PYTHONPATH=. .venv/bin/python -m nba.daily.avg_shadow.install_check`. Expect `avg install 2026-11: ok`.
5. Record (the only database write; outside the game window, with no pipeline job holding the lock):
   `uv run --no-sync python -m nba.daily.avg_shadow.meta bundle data/avg_shadow/bundles/2026-11` (weights shas, model_id, gate id).
   Add a DECISIONS row and a ledger line naming the model_id. Update PROJECT_STATUS section 7 item 2.
6. **How staleness clears, and the proof the new weights are served.** Nothing is rewritten. Rows from 11-01 until the install stay stale for good. The next pretip run
   selects the November bundle (`select_bundle`: month 2026-11, `installed_at <= made_at`, fit_through < 11-01) and writes `stale_model = 0`.
   The proof, in increasing strength:
   - (a) `data/ops/heartbeat/avg_shadow.json` shows the new `model_id`, `month_block` "2026-11" and `"stale_model": false`.
   - (b) The watchdog's 6-hourly "serving stale weights for 2026-11" line stops in `data/ops/ALERTS.md`.
   - (c) The next export's `manifest.json` shows `primary_sources.fallback_stale` near 0 for the 11-18 slate, and `prop_grids.bundle_month` = 2026-11.
   - (d) Read-only, outside the window: `select stale_model, model_id, count(*) from forward_avg_shadow where tipoff_utc >= '2026-11-18' group by all` shows only `stale_model = 0` rows with the new id.

### 2.4 The loose end: the AVG gate refit ("live-row loader + 19->199 support bridge before 12-01")

- **What the rule text requires** (AVG_SHADOW_LIVE_2 item 3): on the 1st, refit the T442 gate on every strictly earlier row (30-day blocks, a 3,000-row minimum). The first live gate came
  from the rebuild's 2023-2025 OOF rows; "later months add the shadow's own stored SEQ and A0 forecasts of earlier months".
- **What is missing.** (1) `gate_rows.npz` (the 2023-2025 aligned rows) was deleted on 10-14 by design. Rebuilding it re-reads the 2025-bearing OOF file and
  regenerates the 2025 A0 members. That is a season-2025 training-class use and needs a HOLDOUT_ACCESS_LOG row first; row 72 covers the original use, and the archivist decides
  whether a repeat is covered. (2) No loader turns `forward_avg_shadow` + `forward_avg_scores` rows (eligible, on time, played, y) into the gate's
  aligned form. (3) **The support bridge.** `fit_gate.py` trains on *continuous* 199-point A0 and SEQ grids. The live table stores `seq_q19` and `a0_q19`, which are 19
  *integer* quantiles. The natural bridge is `grids.ext19(q19)`, the construction the shadow already uses for A0/FULL inside the mixture. It is lossy, and the rule does not name it, so the bridge is a
  procedure choice. It must be written down (DECISIONS, before any December result exists) and reviewed. (4) Probably the five gate context columns
  (`log_n_prior, starter10, n_rep_out, has_report, team_game_no`) are not in the live table. The rule's column list does not include them, so the loader would rebuild them as-of. I did not check this against the live schema.
- **Size.** About 1-1.5 modeler days, plus an adversary review: the loader with planted-future and key-alignment tests, the bridge, the context columns, the
  `gate_rows.py` rebuild (heavy lock, Mac CPU, after the holdout row), `fit_gate.py`, and the assembly. It needs read-only `nba.duckdb` outside the game window. It is not a Colab job.
- **Does it block the November weights? No.** November carries the October gate by Devin's 11-08 decision. It touches only the **December** gate.
- **My recommendation:** carry the October gate again for December, with a new DECISIONS row written before 12-01, disclosed in the verdict note. Reasons: the 01-10 look is
  already INCONCLUSIVE on staleness (2.5), so a rule-text gate buys no verdict there. Rebuilding costs a fresh season-2025 OOF read. And one month of live rows, at about 2-3 thousand per stat (right at the
  3,000-row minimum), is small next to the offline rows; that last point is an expectation I have not measured. If Devin prefers the rule text, the work above must start by about 11-24 to be reviewed by 12-01.

### 2.5 What the delay has already cost (descriptive, from the schedule; rows are assumed proportional to games)

Regular-season games in `live_tips_2026-27.parquet`: 1,200 (10-20 to 04-11; the remaining NBA Cup games may not be listed yet, and the league plays 1,230). Stale games are those from
11-01 up to the day the install takes effect. The rule's limit is "more than 10% of eligible rows stale at the look gives INCONCLUSIVE".

| Install takes effect | Stale games | Share at the 2027-01-10 look (553 games) | Share at season end (1,200 games) |
|---|---|---|---|
| 11-18 (installed tonight) | 122 | 0.221 | 0.102 (0.099 if 1,230 games) |
| 11-19 | 134 | 0.242 | 0.112 |
| 11-21 | 147 | 0.266 | 0.122 |
| 12-01 (December only) | 215 | 0.389 | 0.179 |

So the **2027-01-10 final look is INCONCLUSIVE whatever happens**. Under the rule, an INCONCLUSIVE final leaves one second look at the end of the season, and that look
clears the stale limit only if the install is effective for 11-18 *and* every later month is installed on time. Even then it sits at the boundary.
**What Devin should know:** the refit is still worth its 5 minutes, because it puts AVG back in the app and keeps the second look possible. But AVG_SHADOW_LIVE_2 is now very unlikely to
produce a verdict this season. A clean test would need a **new rule** (for example AVG_SHADOW_LIVE_3 with a counting start after this install). That is a new question,
Devin's call, and not drafted here. My opinion: decide only after December's install proves the monthly operation can run on time.

### 2.6 December (due 2026-12-01 by 12:00 CT), so it is not missed again

- **Before 12-01:** the archivist logs `AVG_SHADOW_LIVE_2 monthly refit 2026-12` in HOLDOUT_ACCESS_LOG (the notebook refuses without it). Devin decides the December gate (2.4) in a DECISIONS row.
  Optionally, merge `agent/steward-avg-monthly-refit` into main (resolve the two docs to main's side; it adds an unwired `nba/daily/avg_shadow/install_check.py`,
  so it needs the replay oracle and Devin's go) so that December runs from the main checkout.
- **12-01, after the morning job has settled 11-30 (about 06:30-07:15 CT, or between pretip slots; read-only, about 1 min each), main session:**
  ```
  cd /Users/devin/Downloads/nba-wt-avgrefit && mkdir /Users/devin/Downloads/nba-prediction/data/ops/heavy.lock && PYTHONPATH=. .venv/bin/python -W ignore research/colab/jobs/avg_shadow_monthly/export_monthly.py --month 2026-12 --db /Users/devin/Downloads/nba-prediction/nba.duckdb ; PYTHONPATH=. .venv/bin/python research/colab/jobs/avg_shadow_monthly/stage_monthly.py --month 2026-12 ; rmdir /Users/devin/Downloads/nba-prediction/data/ops/heavy.lock
  ```
  If `heavy.lock` exists, wait 120 s and retry. Expect about 4,500 to 5,000 November played rows over 215 games (October ran 22.6 rows a game) (`max_date` 2026-11-30, `without_real_tip` 0).
  `stage_monthly` refuses unless the November bundle is installed. Then Devin repeats 2.2 with `2026-12` in both rclone paths, and Claude repeats 2.3 before 12:00 CT.

---

## Part 3: "calibrated always": what the system shows that is not calibrated today

Counts are descriptive and come from the scratch export copy (11-17 slate: 5 games, 149 players x 4 stats = 596 primary latest prop rows). They make no accuracy claim.

| # | Probability shown or used | Calibrated today? | n (11-17 unless stated) | What closing it takes |
|---|---|---|---|---|
| 1 | Prop P(over) at the **DraftKings main line** (`line_cal`) | yes where present | 65 / 596 prop-stats (pts 18/149, reb 17/149, ast 16/149, fg3m 14/149). 241 had *some* DK main quote, so 176 more have a DK line but no calibrated chance. Over 11-10..11-17: 496 / 6,752 distinct props (manifest) | **Pairing, not the map.** `line_cal_fields` pairs only quotes from the production row's *own run*, received by its `made_at`. The latest production row is often written (write-on-change) before books post. Fix: an export-contract change in `nba/export` (market/steward area): pair with the newest DK main quote received before the production row is superseded and before tip. The map stays frozen. It does not touch LINE_CAL_LIVE's scoring (which reads Pinnacle t60 from the scorecard), but it is a display change: adversary review and Devin's go |
| 2 | Same, on **11-14 and 11-15** | no | 0 / 632 and 0 / 1,068: no sportsbook lines at all (odds key deactivated) | operational; fixed by the key swap on 11-16 (lines are back on 11-17) |
| 3 | **Kalshi "N+" rungs** (cards, JARVIS, Explore) | mostly no | 122 / 1,025 distinct (prop, line) have a calibrated number at that line: pts 26/250, reb 39/276, ast 26/214, fg3m 31/285. The other 903 are model only | `rung_cal` covers only DK-listed rungs with raw P >= 0.5, by the ALT_RUNG_CAL fit support (T494; WOUNDED: 92% of its gain is the main line). Higher rungs (raw P < 0.5) need **a new pre-registered rung test** on that support; the frozen map cannot be stretched |
| 4 | P(over) at the **Pinnacle main line** | no (the export applies DK cells only) | 176 props had a Pinnacle quote | the map *has* Pinnacle cells, but serving CAL at Pinnacle's line is exactly LINE_CAL_LIVE's "TRANSPORTS" action (section 9). **Blocked by the lock until the look** (projected 12-01; zero lines on 11-14/15 slow the 5,000-leg count, so likely later) |
| 5 | **Under** side at the main line | complement of the calibrated over (1 - p) | as row 1 | LINE_CAL_LIVE's side checks (P2/P3) decide it; PARTIAL allows one LINE_CAL_SIDE follow-up |
| 6 | Whole-number lines | no (the map is x.5 only) | 0 tonight | new test; rare |
| 7 | **Win probability** (`rung0_injury_elo`) | no post-hoc map; the logit model is backtest-calibrated (T128) | 5 games | WINNER_RECAL fails its own power gate (0.15-0.23) and is not frozen. The cheap honest option is its "no-harm-only" variant (option 2 in that draft): Devin's choice |
| 8 | **P(plays)** shown and multiplied into card prices | no map. Production is the raw hurdle (Platt off). The app shows R for 143 of 155 players and production for 12 | 155 players | No calibration map exists. R's live calibration is read under PPLAY_INTO_PROPS_LIVE (frozen). **Repo inconsistency to settle:** `nba/props/forward.py` says raw ECE ~0.01 and the Platt-mapped ~0.14, while `docs/prereg/PPLAY_INTO_PROPS_LIVE.md` line 35 calls production's *raw* p "ECE ~0.14". One of them is wrong; I did not resolve it |
| 9 | **AVG distributions** once they serve again | `line_cal` / `rung_cal` are always built from the *production* row, even when the shown distribution is AVG | 548 AVG rows tonight (all fallback) | After the November install the card will show an AVG distribution beside a calibrated chance computed from production's P(over): two models on one card. Label it, or show AVG model-only. Display choice for Devin; no AVG line map exists |
| 10 | Combo props (PRA, PR, PA, RA), blk / stl | no | COMBO shadow rows (not counted here) | COMBO_LINE_CAL is a draft and not freezable (it fails its gate). Combo mode's legs use the banded LINE_CAL map, but triples are "model estimates" and the script shifts are exploratory |
| 11 | **JARVIS** "model X%" text | raw (`forecasts.py:105`: raw `leg_marginal` x P(plays)) | every play | The fair price JARVIS *labels* with is Pinnacle no-vig, because w = 0 for every stat (11-16 weekly evaluation: hold), so raw numbers do not move a label today. Showing `P(play) x CAL` is the LINE_CAL_LIVE TRANSPORTS wiring, blocked until the look. Where Pinnacle has no line, JARVIS measures no edge (it does not fall back to raw) |
| 12 | **Halftime overs** (T508 arm M) | Platt fitted 2019-2024 (backtest-calibrated); live unproven | per halftime | HALFTIME_OVERS_VS_PRICE is a draft; freeze it before any claim |
| 13 | **Parlay engine** (`nba.parlay evaluate`) | raw marginals from stored grids, widened by `calibration_logit_sd`, shrunk to market weight 0 | 2,155 evaluated tonight, verdict `no_positive_ev_found` | Verdicts are the market's while w = 0. `top_parlays` uses `p_cal` where a DK leg has one. PARLAY_JOINT_CAL (draft) would test joint calibration |

**Opinion.** "Calibrated always" is reachable for the main line only, and only in steps. (a) The pairing fix (row 1) is the cheapest real gain: on 11-17, up to 241 props instead of 65
(about 3.7x) would get the existing frozen map, with no new rule. (b) The LINE_CAL_LIVE look decides whether that map can be trusted live at all; until then, "calibrated" means
"historically calibrated". (c) Everything off the main line (rungs, unders below 0.5, combos, P(plays), wins) needs its own pre-registered test.
Until those exist, the honest state is the app's current "model only" label, not a refit. Refitting a map tonight would break a frozen test and
claim nothing.

---

## Playbook checks (docs/MODEL_PLAYBOOK.md)

- **Neural sequence (AVG E2 + T442 gate).** Information or capacity: capacity plus prior-season history; the refit adds the newly played October rows (more
  data, the class's main driver). Enough data: the warm block adds 1,990 rows to a W_ALL pool from 2019-20 on; the history gain is largest early in the season, which is
  the stretch the staleness lost. Test validity: the live log is the only clean venue (2025 is spent); the stale-row integrity gate now dominates (2.5); the November gate deviation
  is disclosed. Lineage: ENSEMBLE E3 / T478; era channels are 0 on live rows; P1b (CPU vs GPU) was proven for October only, and **no monthly CPU-GPU check exists** (runbook open question 4).
  Scored against: integer CRPS vs FULL and A0 on the 19-atom form; G1 on the stored 199-point pmf.
- **Gradient-boosted trees (production props, FULL, P(play) R, minutes).** They refit daily on rows before the slate, so "retraining" is already continuous. Tuning
  rarely matters (60-trial searches added nothing). No refit is due. Test validity is unchanged (real tips, as-of report gating).
- **Ratings (injury-Elo).** Low data appetite; refit every run; nothing to retrain. Calibration has no live map (Part 3 row 7).
- **Quantile and distribution layers (LINE_CALIBRATION, ALT_RUNG_CAL).** Driven by per-slice calibration volume and stability over time. Both are static 2023-24 maps under a
  frozen live test; the right next step is the LINE_CAL_LIVE look, not a refit. Coverage, not the map, is tonight's gap.
- **Set / joint (parlay copula, combo params).** Effects are below one-season detectability; there is no cadence rule and nothing is due. The market weight of 0 keeps raw marginals out of verdicts.
- **Count models (COMBO NB).** The NB heads refit daily and R is frozen by the rule; nothing is due.

## Proposed rows (drafted here, not appended)

**PREDICTIONS_LEDGER** (priors stated before any result of the November refit):

| Date | Rule / question | Prior stated before the result | Where written | Outcome | Hit? |
|---|---|---|---|---|---|
| 2026-11-17 | AVG Nov bundle installed and served: the first pretip after install writes month_block 2026-11 rows with stale_model = 0 under the new model_id | P = 0.85 (risks: Colab Drive mount from VS Code, the possessions check, operator timing) | docs/reviews/retrain_survey_2026-11-17.md | pending | |
| 2026-11-17 | AVG_SHADOW_LIVE_2 final look 2027-01-10 is INCONCLUSIVE on the >10% stale-row integrity gate | P = 0.97 (projected stale share 0.22 or more already) | same | pending | |
| 2026-11-17 | AVG_SHADOW_LIVE_2 season-end second look has stale share <= 10% (if reached) | P = 0.25 (needs the install effective for 11-18 and Dec-Apr on time; 0.099-0.102 even then) | same | pending | |
| 2026-11-17 | LINE_CAL_LIVE look happens on or before 2026-12-01 | P = 0.35 (two nights with zero sportsbook lines; leg counts not read) | same | pending | |

**DECISIONS** (for the main session, after Devin's go): "2026-11-17: AVG November weights refit from the 11-08 staging (rows < 11-01, 1,990 rows, data_version 22566ef2b04a), October gate carried
(11-08 11:15), assembled from agent/steward-avg-monthly-refit; installed <ts>; model_id <id>. Rows 11-01..<install> stay stale. Projected stale share at the 01-10 look of 0.22 or more, so that look is
INCONCLUSIVE; the season-end look is borderline. | Follows advice (NEXT_SESSION 11-08 item 2). | Reversed by: none (weights are never overwritten); a new AVG rule would be a new id."

**HOLDOUT_ACCESS_LOG:** none for November (row 188 covers it). December needs its own row before staging (archivist).

## Not done, and why
- No fit, no Colab, no install, no database open (game night; Devin's instructions). No network: the rclone transfers are Devin's.
- I did not verify that Drive does not already hold a 2026-11 folder from an earlier attempt. `rclone copy` is idempotent, and the notebook picks the newest staged folder by mtime.
- I did not verify the possessions completeness, the live `forward_avg_shadow` schema for the gate context columns, or the LINE_CAL_LIVE leg count; each needs `nba.duckdb`.
- While reading the job folder I ran `build_notebook.py --help` in the `nba-wt-avgrefit` worktree. The script has no help flag, so it ran its build, but it failed inside `ruff format` before writing anything; `git status` shows the notebook unchanged.
