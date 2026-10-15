# Next session

_Updated 2026-10-14 20:27 CDT. Status overview: PROJECT_STATUS.md. Retro: docs/reviews/retro_2026-10-13.md._


## OVERNIGHT 2026-10-14 -> 10-15 (Devin asleep; nothing merged to main, no holdout, no live DB writes)
A1 merged by Devin (70c52c0). Six agents launched ~22:30 CDT, each on its own branch:
1. agent/modeler-f1-warm-window: apply the 10 F1 pre-freeze edits + data gate numbers; Devin's 3 open calls marked OPEN (follows review b02d403).
2. agent/steward-best-model-minors: morning-report primary-source counts, post-tip `>` vs `>=`, p_play for dropped no-team games; oracle 89889f6d; needs Devin's go by Fri 10-17.
3. agent/modeler-avg-november-prep: gate_rows.py explicit join order (review S1) + staged November refit notebook/runbook + missing-bundle alarm (deadline 11-01 12:00 CT).
4. agent/modeler-pover-bias: SCRATCH diagnostic of "P(over) 3-4 pp low at DK lines" on 2023-24 only; overlap with T477/LC2.
5. agent/archivist-questionable-scope: inventory of QUESTIONABLE/DOUBTFUL statuses (Claude's suggestion, not approved by Devin; scoping only).
6. agent/scribe-retro-1014: retro for 10-14.
Results are summarised here as they land.

## 2026-10-14 late evening (after "gnn"; Devin back)
Done (DECISIONS 20:31 and 21:30; main e35dce9):
- **App shows the best-evidenced model (Devin: "get rid of the timeline, best model into the front end asap").** Props from AVG
  (T478, the only 2025-holdout pass) when a fresh pre-tip regular-season row exists and production's inputs are unchanged; else
  production with a stated reason; P(play) from R unless out on the report (0%) or R is behind production. Export 15175c1 + app
  6bea14c MERGED after review BLOCKED x2 -> APPROVED (docs/reviews/best_model_e2e_review_2026-10-14.md). Scoring untouched.
  No AVG/R rows exist yet (preseason); opening night is the first real check. AVG needs the November bundle before 11-01 or the
  app silently falls back to production everywhere.
- **A1 built and APPROVED** (agent/steward-export-gate 92ab046): export gates on morning data steps, Drive-backup failures exempt.
  NOT MERGED: Claude's merge was blocked by auto mode (live path ops/). Devin runs:
  `git merge --no-ff origin/agent/steward-export-gate -m "merge agent/steward-export-gate: export gates on morning data steps (A1)" && git push`
- **WOWY_STRUCT / F1:** power re-measure done from stored grids only (agent/modeler-wowy 941fa76; all cells >= 0.5, pts top-25%
  2023 thin at 0.55, kept). It is a proxy: a 2019+ A0_warm fit IS F1's arm C, so order is F1 edits -> Devin signs F1 -> freeze ->
  F1 run -> strict WOWY re-measure from C's forecasts -> WOWY freeze. F1 pre-freeze review APPROVED WITH EDITS, 10 edits
  (agent/adversary-f1-prefreeze b02d403), not yet applied. Devin open: F1-first order, count floors -0.0025 (Claude: keep),
  WOWY E7(e) wording (only F1 licenses a window change).
- **Parlay context:** EV closed; PARLAY_CONTEXT_PROB gate FAILED on power (best 0.24; agent/market-context-prob 02797f8), not
  frozen, closed. Lead: model P(over) 3-4 pp too low at DK lines (overlaps T477/LC2); check before any new rule.
- Still Devin: AVG meta records (meta bundle, five proofs, counting-start 2026-10-20); COMBO counting start; eyeball the new model
  sentences at 375 px light/dark (Today, Game pages).
- Loose ends (review minors): p_play rows for dropped no-team games; reader production post-tip check `>` vs `>=`; morning report
  should show primary-source counts (pts silently falls back if the _lt model stops logging).

## STOPPED 2026-10-14 ~20:27 CDT (Devin: "gnn")
Done this evening (commits 7ae5b55..c182440 on main; DECISIONS rows for each):
- **Wiring branch MERGED** to main 1dbee39 on Devin's conditional go: steward fixes 3ccacde (S1 budget 420 s, S2 counting_start
  filter in AVG/COMBO/FULL readers, S3 interim kill, T1, M5-M7, A2), main merged 9bf2cf7, oracle_w3 IDENTICAL 89889f6d, checks
  green (479 tests daily/eval/ops/layering). Replay copies deleted by Devin (holdout log bbdb557). Export plist installed by Devin;
  first run SKIPPED correctly (morning rc=1 from the Drive backup), so A1 is now concrete: Claude still recommends gating on data
  steps; it needs a replay + Devin's go by Fri 10-17 or waits until after opening night.
- **Still for Devin after the merge:** AVG meta records (`meta bundle data/avg_shadow/bundles/2026-10`, five proofs,
  `meta counting-start 2026-10-20`), COMBO counting start likewise (never before 10-20). AVG needs a November bundle before 11-01.
- **WOWY_STRUCT** (origin/agent/modeler-wowy c44ee72, NOT frozen): pre-freeze review APPROVED WITH EDITS (f1f0add); 2022 CRPS
  conversion check passed (t_c 0.0050/0.0063/0.0030 stand, all within +-20%, pts leader-out at 0.82); E1-E7 applied; E7 = every
  arm trains from 2019-10 (Devin); mixture refit per block on 2019+ (Devin); G2 = S+R vs A0_warm all rows (Devin); star out =
  largest single OUT player's v_s >= 2020-21 rotation 0.75 quantile (15.13 pts / 3.45 ast; overlaps its cells 91-100%, weak
  guard); heavy absence (sum, 45.0 / 10.4) reported only. Remaining before Devin signs: (1) **Devin's go on the 2019+ power
  re-measure** (forecasts only, NN8; Claude: yes); (2) short adversary pass on the final text incl. the new Vmax column
  (planted-future not re-run on real rows); (3) sign together with F1.
- **F1_WARM_WINDOW** (branch agent/modeler-f1-warm-window): draft RUNNING at stop. Shares fits with WOWY_STRUCT, so F1 must be
  FROZEN BEFORE any WOWY_STRUCT arm is fitted. Mixture choice must match WOWY E7(b) (refit per block).
- **Parlay context copula** (b6e1fca): read. Real but small (36 cells vs ~5 under placebo; best typical +6.1%, below every
  measured cost). Claude recommends closing as a sizing null; Devin has not answered.
- Claude's open suggestion: scope the "questionable" players blind spot (features only count OUT at T-60).
Next session first: F1 draft result -> Devin's go on the power re-measure -> adversary pass -> Devin signs both; A1; parlay null.

## STOPPED 2026-10-14 ~19:15 CDT (Devin ended the session)
Earlier today (recorded in TEST_LEDGER / DECISIONS): LC2 closed (DATA, FLAT); ALT tests all NOT PROFITABLE (~vig); AVG2 frozen and
installed (bundle b8d2b27b, 16:16 CDT; counting start UNSET); COMBO frozen; RAPM untestable; F12 closed; app on/off + badge merged.

This evening:
- **Steward wiring branch agent/steward-opening-wiring (da08b57):** COMBO + AVG2 flags ON in pretip, export launchd job, watchdog
  drill. Replay oracle main vs branch both IDENTICAL 89889f6d (oracle_w_main, oracle_w_branch2). P3 (combo writer ON) IDENTICAL,
  heartbeat ok 96/96 runs, rows on 20/20 dates; combo mean 3.1 s / max 10.6 s per run; 46 MB over 20 dates. Review (adversary,
  origin/agent/adversary-wiring-review d482261): WOUNDED, APPROVED; S1 (no time budget), S2 (readers score rows before
  counting_start, AVG/FULL too), S3 (interim kills on coverage alone), T1 (vacuous test), M1-M7, A2 (kickstart after bootstrap).
  Steward was RESUMED to fix S1-S3, T1, M5-M7, A2; status unknown at stop. Next: read its result, re-run the oracle on the fixed
  branch, merge with Devin's go BEFORE Sat 10-18 (hands-off). Steward worktree data/ is a symlink to main data/ (old local copy in
  scratchpad steward_worktree_data); never stage data/ from it.
- **Devin decision pending A1:** should the app export run when the morning rc=1 came only from Drive backups (today: DNS outage
  09:19, every data step rc 0)? Claude recommends yes (gate on data steps). Devin said "I don't know"; strict rc==0 kept for now.
- **After merge, Devin runs:** `cp ops/launchd/local.nba.app-export.plist ~/Library/LaunchAgents/ && launchctl bootstrap
  gui/$(id -u) ~/Library/LaunchAgents/local.nba.app-export.plist && launchctl kickstart -k gui/$(id -u)/local.nba.app-export`.
  Then the AVG meta records (writes live DB, Devin's go): `meta bundle data/avg_shadow/bundles/2026-10`, the five proofs,
  `meta counting-start 2026-10-20`; COMBO counting start likewise, never before 10-20. AVG needs a November bundle before 11-01.
- **WOWY:** history version NOT frozen (gate fails; big with/without gaps are noise). App shrinks those numbers (merged 20ac848).
  Devin's per-possession idea **WOWY_STRUCT** (draft, origin/agent/modeler-wowy b5a5569): leak audit WOUNDED, no leak
  (origin/agent/adversary-struct-leak 6d3a3bf); 3 cells survive (pts leader-out, pts top-25% fixed 5.04, ast leader-out), primary
  contrast STRUCT beyond role share; permutation null done (8/9 beat all 30 draws). Pre-freeze review RUNNING
  (agent/adversary-wowy-struct-prefreeze); it must reconcile rule line 6 (G1 flat -0.003 vs measured 0.0049 / 0.0063 / 0.003).
  Then Devin signs (Claude: keep ast leader-out), freeze, write harness, reproduce gate from main, run (2023 select, 2024 report).
- **Devin decision pending F1:** props feature frame starts cold in 2022 (n_out_rot differs on 32.6% of 2022 rows vs a
  history-inclusive build; every refit trains on them). Claude recommends a November test (warm frame over data/history + nba.duckdb).
- **Parlay agent agent/market-context-copula (Devin's idea):** does context (teammate out, leader out, blowout) change cross-leg
  residual correlation enough to move a same-game parlay past the vig? Gate/inventory only; RUNNING at stop.
- **Devin deletes (agent rm is blocked), all season-2025 replay copies, unread:** `rm -rf data/rehearsal/oracle_w_src.duckdb
  data/rehearsal/oracle_w_main* data/rehearsal/oracle_w_branch* data/rehearsal/oracle_c2*` (HOLDOUT_ACCESS_LOG rows 05e1b50).
  Wait until the fixed-branch oracle re-run is done; it needs a fresh source copy anyway.
- CI on abcb6e0: lint-type and data-contract green, unit was still running (40+ min); check the latest run on main.
Next session first: steward result -> oracle re-run -> merge (Devin's go) -> export plist -> AVG/COMBO meta; WOWY_STRUCT review
-> Devin signs; parlay agent result; A1 and F1 answers.

## SIX-DAY PLAN to opening night (written 2026-10-14; production stays on the MacBook)
| Day | Claude / agents | Devin |
|---|---|---|
| Tue 10-14 | LC2 re-score (fixed scorer) + adversary attack + ledger; AVG_SHADOW_LIVE_2 freeze if signed; steward re-runs P3; pretip dry run (slate 0) with all new code; launchctl / registry / full suite + CI green; fix the 10 mypy errors | "sign AVG2" or no; run the scratch cleanup command |
| Wed 10-15 | AVG Colab rebuild staged; P1a/P1b proofs after it; incident-log and retro catch-up | click Run on the rebuild notebook (~45 T4 min) |
| Thu 10-16 | if all AVG proofs pass: PR-style diff to add --log-avg-shadow to ops/nba_daily.sh, replay oracle before/after; watchdog drill (stop a job, confirm the alert); ingest-queue game-day decision | approve or decline AVG wiring (a production change) |
| Fri 10-17 | last change day: CI green, backups verified, tag opening-night-2026-10-20, opening-night checklist final | read the checklist (5 min) |
| Sat-Mon 10-18/19 | hands-off: nothing under nba/, ops/, configs/, uv.lock; monitoring only | Mac on AC, lid open from 10-19 |
| Tue 10-20 | opening night: probe the first 2026-27 injury-report PDF in the morning (none published as of 10-14; parser only tested on 2025-26 format), supervised pretip run, ALERTS, forward rows before tip, shadows writing, odds file shapes | 5-minute check after first tip |
Not in the six days: mini PC, app/PWA work, reporter feed (needs Devin's X sign-up; optional for opening night).

## PAUSED 2026-10-13 ~16:05 CDT (Devin heading home)
- SEQ_LEARNING_CURVE_2: FROZEN 86f31649 (cf2ba61), loader review APPROVED (93dd7c0), Colab run done 98.5 T4 min (pulled to
  data/colab/runs/seq_learning_curve_2/20261013_134937/). Scoring was running on the Mac (modeler; log in the session
  scratchpad); verdicts not yet read. Open: cap diagnostic 5/30 (Colab file) vs 2/30 (scorer), both under the 1/3 limit.
  Next: results section, TEST_LEDGER rows, prior scored, then the adversary's post-result attack. T4 units/hour still unread.
- AVG_SHADOW_LIVE: built, flag OFF, uncommitted; /code-review found 10 issues (one could break the morning briefing);
  steward fixing. P3 FAILED (19-atom G1 calibration, q10 0.126): counting cannot start; repair = new rule id, Devin's call;
  check whether FULL_SHADOW_LIVE has the same 19-atom issue. Do NOT run the AVG rebuild notebook until then.
- Waiting on Devin: Oracle VM + stats.nba.com test; P3 repair decision; old co-author trailers (Claude: leave).

## Start here (handoff 2026-10-13 08:55 CDT)

Session 2026-10-13 morning, commits e333157..7e77efd (plus this one). Nothing is running.

Done:
- History position audit SURVIVES (docs/reviews/history_position_audit_2026-10-13.md). FULL/ROUTER/ENSEMBLE etc. never read history;
  only W_ALL (T478/T483/T485) had 322/77,195 training rows (0.42%) with no position, no eval exposure. Tree path saw NULL, not 0.
- Devin's decisions (DECISIONS 07:06-07:15): P(play) switch waits for the live log (PPLAY_INTO_PROPS_LIVE; the live arm is the
  2022-start R, not R19); T478 AVG to a live shadow; SEQ_LEARNING_CURVE_2 approved for drafting. `sudo pmset -c sleep 0` done
  (verified: AC sleep 0; battery still sleeps at 1 min and lid close sleeps, so plugged in + lid open on game nights).
- Holdout log: disclosure row for the audit's 2025 row-count query (counts only).
- 2013-14 and 2014-15 loaded into the history DB (b8a0db7, data_version 82ce3683ec29; backup
  data/backups/nba_history_pre_2015_19_ingest_20261013T122611Z.duckdb, tag name is the loader default). Static fetch widened to
  2013-21 (b2c20d1) and run by Devin 07:43-08:04.
- BEST_PRACTICES: check a command's scope before handing it to Devin (two wasted Devin runs this morning).
- AVG_SHADOW_LIVE drafted, BLOCKED, fixed, re-review APPROVED (7e77efd; docs/reviews/AVG_SHADOW_LIVE_prefreeze_2026-10-13.md). Not frozen.

SEQ_LEARNING_CURVE_2 gates (draft docs/prereg/SEQ_LEARNING_CURVE_2.md):
- D4 position: 0.33% unknown (13 ids, 550/165,750 rows): PASS (needs <= 1%).
- D3 reconcile (reports/history_2013_2014_boxsample_reconcile.json, 125 games): missing/extra 0 PASS; minutes 99.4% PASS;
  pts/ftm off by one: 2013-14 1.22% (17/1397) FAIL read strictly, 2014-15 0.64%, pooled 0.95%. The rule does not say per season
  or pooled; read it per season (do not pick the passing reading after seeing it) and fix that wording before freeze.
  Every mismatch is derived = official - 1 FT (derived never higher); player 202326 three times; 2015-18 was 0.34%.
- NEXT (Devin agreed, "in some hours"): archivist repairs the 2013-14 free-throw parse (likely an unmapped FT wording, like the
  13 shot codes), offline against the cached boxsample (no network, no Devin command), backup + data_version bump, re-reconcile.
  If 2013-14 still fails: Devin's fallback is 2014-15 only, windows 8 and 9 (E becomes 9 v 8, near-certain FLAT/AMBIGUOUS);
  record it in DECISIONS as Devin's call. Then: normalizer constants for new seasons, the review-gate fix (exact whole-line
  Rule: match; deleted/renamed later review counts as BLOCKED), pre-run loader review with Verdict: APPROVED, freeze.

Waiting on Devin:
1. AVG_SHADOW_LIVE: sign off the rule; approve the drafted holdout-log row (text at the end of the rule: 2025 labels as training
   rows for the Colab rebuild, 2025 OOF consumed only by the T442 gate fit; no 2025 forecast scored). It must be committed BEFORE
   the rebuild. After the rebuild, 2025 is in-sample for AVG: the live log is its only clean test. Then freeze; steward builds
   (4 tables, pretip CPU inference, Colab rebuild with weights saved, monthly refit, scorer, watchdog, 4 parity proofs; oracle
   89889f6d must not move). Before the rebuild, check the live gate's inputs equal T442's exactly (reviewer's open point).
2. Keep the week-12 look floor (Claude: keep). 3. Colab unit rate (configs cost.yaml has null rates).
4. Force-push: e333157 carries a Claude co-author trailer (against the no-attribution rule; pushed). Amend + force-push main, or leave.

Loose ends (recorded, not chased): AVG non-blocking R3-R5; the 2013-15 changelog entry is marked DRAFT; 13 history ids with no
position; one 2014-15 game with no derivable starters/stints; mypy 10 errors in research/eval/pi_transfer_diag.py; "18 arms" typo
in the SEQ_LEARNING_CURVE job description; rclone client_id; nba/truth/bootstrap.py does not exist.

## Before opening night (first games Tue 2026-10-20)

1. DONE: facts store live-season build (fa81e35 on 2026-10-11; holdout guard + 14 tests d32464a on 2026-10-13). NBA_FACTS_NIGHTLY=1 only on the VM.
2. Reporter feed first poll after Devin's X sign-up (X_BEARER_TOKEN in .env, handles in configs/reporters.yaml; `--budget-posts 50`; shadow only).
3. `launchctl list | grep local.nba` shows daily-pretip, daily-morning, lineups, kalshi-snapshot; `uv run python -m nba.registry list`
   shows rung0_injury_elo v2 and props_context_residual v2 as production.
4. Ingest queue on game days (data/ops/ingest_queue_status.md): confirm daily and lineup jobs get through; decide whether to pause it.
5. Maintainer decisions (PROJECT_STATUS section 6): VM, rclone own client_id, checkpoint cleanup, integer-quantile route.

## Opening night 2026-10-20

- Supervised pretip run before the first tip: `uv run python -m nba.daily run --date 2026-10-20 --roster-source official --log-int-variant`, then data/ops/ALERTS.md.
- Mac on AC, lid open (AC sleep is 0; lid close and battery still sleep).
- Morning job: settle/report, no alerts, backup exists. Pretip hourly from 09:30: a forward row per slate game before tip, official roster, none after tip.
- Injury report fetched; lineups snapshots and T-30 shadow rows; T-15 arm decisions and lineups_t15 heartbeat; integer-variant rows;
  parlay shadow rows; Kalshi snapshot writing; P(play) shadow rows (R, C, C'). Run summary `dirty=False`.
- Open the first live odds raw file: 89 markets x 12 points, new shapes parse; watch live_book_odds growth.
- If Kalshi lists player props, alias review before any match is trusted. Next morning: settled rows, forward report, DNPs settle as DNP.

## First two weeks

- Official roster source through 2026-11-03, then `recent` automatically; compare coverage and rookie minutes error.
- Weekly forward health check (steward): calibration, bias per stat, production vs comparison models. No win/loss read on small n.
- Weekly: re-check the 30 missing 2026-27 tip rows (Devin, 2026-10-12).
- PPLAY_INTO_PROPS_LIVE first look fg3m ~Sun 2026-11-15; AVG_SHADOW_LIVE (once frozen) interim kill-only week 4, final Sun 2027-01-10.

## After opening night

- Score shadow arms forward (integer quantiles, T-30 lineups) with paired clustered bootstrap; promotion on live data only.
- Platt P(play) forward check before trusting unconditional parlay probabilities.
- Parlay stays shadow until the deferral gate (30 settled rows, 14 dates) and positive EV at the conservative bound; else "no positive EV found".
- SEQ_MINUTES after the P(play) decision; PBP_HALFTIME last. Pending Devin: RECENT_ROLE_FULL Stage 2 vs waiting for the live log.
- When production moves to the VM: NBA_FACTS_NIGHTLY=1.
- parlayhitters.ai build after 10-20 (designs in docs/BUSINESS_DRAFT_2026-10-11.md, APP_ARCHITECTURE, APP_SECURITY_BASELINE section 12,
  APP_CONTENT_SPEC); nothing touches nba/, ops/, configs/ before then. "jev engineering" = route LLM calls by difficulty (Jev-style router: cheap tier for easy, flagship for hard; Devin 2026-10-13); applies to the app's per-token calls, not the Claude Code build loop; confirm exact vendor URL/plan before buying; test routed vs unrouted on real app questions.

## Loose ends (recorded, not chased)
- AVG2 gate_rows.py (review 2026-10-14 S1): polars joins do not guarantee row order; 2025 tail alignment is verified only indirectly. Make the join order explicit (sort by key) BEFORE the November monthly refit.
- 2026-10-14: nba/explain/data.py load_report maps injured players to teams via arg_max(team_id) over all box rows <= game date, so for early 2026-27 games it reads 2025-26 team ids (team only; no outcomes). Systems use of the burned season; moot after the 20-game rollover (~10-23) folds 2025 into tuning. Better long-term: use the 2026 official roster. Not changed before the 10-17 freeze.
- NEW 2026-10-14 (null audit): possessions `assister_id` is NULL for accented passers (Jokic, Doncic at 0%; 91.2% coverage overall); no closed verdict depends on it; fix in the parser's name matching before any assist-network feature (archivist).
- Null audit re-tests queued for after 10-20, one pre-registered rule each: T185 F12 closing risk (gate failed on pre-v2 stints), then T176 RAPM (pre-v2 lineups; check power first); PBP_GPT T082/T084 UNKNOWN pending a code read.

- team_coaches wrong head coach in 14/87 team-seasons 2022-24, no mid-season changes; no production reader (archivist).
- stints miss 40% of <1-minute appearances; rebuild needs Devin's approval (DECISIONS 2026-10-10 ~23:55).
- 167 player_availability rows with NULL game_id stay NULL by design (postponed > 7 days, ambiguous, Cup final not in games).
- Pickle shim nba/sim/usage_redistribution.py stays until every cache post-dates 2026-10-10.
- Pretip launchd fires an hour ahead (Devin left it, 2026-10-10; covers the last tip).
- Loader-gate R1-R3/R5 (stamp transitive dependency hashes).

Finished items and the pre-prune text: docs/archive/next_session_done_2026-10.md.
