# Next session

_Updated 2026-10-13 (pruned to open items). Status overview: PROJECT_STATUS.md. Retro: docs/reviews/retro_2026-10-13.md._

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

1. Facts store live-season build (steward): `nba.facts build --seasons` list (2026-27 report/lineup/box facts in, 2025 absent) plus
   `--schedule-seasons` for 2026-27 `tips_at`; nightly rebuild after the morning settle. Replay oracle `oracle_pplay` golden
   `89889f6d` before and after; the hash must not move.
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
  APP_CONTENT_SPEC); nothing touches nba/, ops/, configs/ before then. Open: what "jev engineering best practices" means (Devin).

## Loose ends (recorded, not chased)

- team_coaches wrong head coach in 14/87 team-seasons 2022-24, no mid-season changes; no production reader (archivist).
- stints miss 40% of <1-minute appearances; rebuild needs Devin's approval (DECISIONS 2026-10-10 ~23:55).
- 167 player_availability rows with NULL game_id stay NULL by design (postponed > 7 days, ambiguous, Cup final not in games).
- Pickle shim nba/sim/usage_redistribution.py stays until every cache post-dates 2026-10-10.
- Pretip launchd fires an hour ahead (Devin left it, 2026-10-10; covers the last tip).
- Loader-gate R1-R3/R5 (stamp transitive dependency hashes).

Finished items and the pre-prune text: docs/archive/next_session_done_2026-10.md.
