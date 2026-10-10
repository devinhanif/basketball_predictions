# NBA Prediction — Project Status

_Living document. Last updated: 2026-10-10 10:45 CT (HEAD c41084b; tags `pre-restructure-2026-10-10`, `live-candidate-2026-10-10`). Earlier narrative (2026-10-08) is kept below under "Background". Sources: git log, TEST_LEDGER (T001-T201), HOLDOUT_ACCESS_LOG, MORNING_BRIEFING_2026-10-10, DECISIONS.md, `nba.registry list`, launchctl, docs/reviews/._

**Since 2026-10-09 evening (all byte-identical on the live path, proven by the replay oracle):** lineup tracker fixed twice (stint reconciliation 45% → 98-99%); eight research→production import edges cut; 98 research modules (40,780 lines) archived under `research/` and tested in CI; the layering test forbids `nba/` from importing `research/`; F9b closed as a null (T186-T201); two odds feeds wired (live Pinnacle benchmark; two-season historical props pull running); F19 "never four givens" measured (the market's safest legs are priced right)._

**Since 02:15:** possession parser in game order and rebuilt (every ordering defect to zero, data_version 85d8a41277ce); the rung-ladder chain, pts_tail and stack/oof archived (nba/ is now only what runs at 7 pm plus its tests); first local game-explainer pages (`nba/explain`); the historical props-vs-market scorer built with production OOF rows reproduced exactly, awaiting the rule's confirmation; REFEREES stopped at its gate on duplicate official ids (T202); launchd found an hour ahead; live-odds key rejected since 08:20._

## 1. In production (registry alias `production`)

| Model | Version | What it does | Headline (OOF 2022-24, real-tip gating) |
|---|---|---|---|
| `rung0_injury_elo` | v2 (v1 deprecated) | MOV-Elo logit + official injury-report availability terms; coefficients refit every run on games before the slate date | Log loss vs MOV-Elo -0.00812 [-0.01212, -0.00379], n=3,951 games (T128, same as T091; v1/proxy19 was -0.00786). Brier -0.00371; ECE +0.0108 [-0.0059, +0.0209] |
| `props_context_residual` | v2 | pts/reb/ast/fg3m quantile distributions from recency average + context residual, injury-report gated | CRPS vs recency_conformal: pts -0.0862, reb -0.0313, ast -0.0229, fg3m -0.0102 (T092-T095, reproduced in T128; clustered CIs in the ledger) |

- Real-tip gating (76cc177, maintainer-approved, T128): training and backtests now admit an injury-report snapshot only if it was published at least 60 min before the real scheduled tip (coverage 1320/1320, 1318/1318, 1315/1315, 1316/1316 games for 2022-25, 0 proxy fallbacks). Removes the train/serve mismatch found by the red team (4d56875); it costs about 0.003 of the pts gain.
- The 2025 holdout figures on v1 (proxy19 gate) are not re-measurable and are carried on v2 only as labelled historical values. season=2025 is burned (no virgin holdout); the 2026-27 forward log is the only clean test.
- Win prob is ~64% accuracy, calibrated; the gain over MOV-Elo is small. Red team (redteam_production_2026-10-09): injury_elo SURVIVES; context_residual WOUNDED (minor, the proxy defect, now fixed).
- Other registry heads: `rung0_mov_elo` v1 is also `production` (fallback/comparison). rung1-4 and seq_props remain candidates, none promoted.

## 2. What runs automatically (launchd on the maintainer's Mac, bridge until a VM is chosen)

| Job | Schedule | Does |
|---|---|---|
| `local.nba.daily-pretip` | at :20 and :50, 09:20-21:50 | `nba.daily run` (official rosters through 2026-11-03 then recent; logs the integer-quantile and lower-tail shadow variants), referee assignments, `nba.odds capture` (read-only sharp-line benchmark from theoddsapi.com, key in `.env`; exit 4 informational), `nba.markets capture`, then read-only `nba.parlay evaluate` shadow log. Exit 4 = some games already tipped, not a failure (5 = degraded). **Open issue:** the 21:20 and 21:50 slots did not fire on 2026-10-09 (not sleep, lock or crash; launchd did not launch); fix proposed in MORNING_BRIEFING_2026-10-10 |
| odds history pull (not launchd; `sh ops/odds_history_pull.sh`, detached) | one-time, 2026-10-10 | the-odds-api.com archive: T-60 and T-5 snapshots of props and game lines for 2023-25 into `data/odds/odds_history.duckdb`; raw cached; credit ledger capped at 2M of the 5M plan. Status: `python -m nba.odds history-status` |
| `local.nba.daily-morning` | 08:00 | settle, report, parlay settle, post-game ingest (skipped while the ingest queue runs), data manifest snapshot/diff/leak check, DB copy to `data/backups/` (7 kept), Drive copy of registry, DB backup and lineup snapshots |
| `local.nba.lineups` | every 5 min, idle outside game windows | T-30 lineup collector (static daily_lineups JSON, one GET per tick); shadow only (0877e89, 2b3cc84) |
| `local.nba.kalshi-snapshot` | every 15 min | read-only Kalshi market/price snapshot to `data/kalshi/kalshi.duckdb` (plist is not created by `ops/install_launchd.sh`; documented in KALSHI_LIVE_2026-10-08.md) |
| ingest queue (not launchd; `sh ops/run_ingest_queue.sh`) | continuous, ~6 s/request (~600/h quota) | 50-step sequential queue; at 13:23Z on 2026-10-09: 2 done (handover, cur:tracking), `cur:hustle` running since 13:16Z, 47 pending. Hustle ~6.5 h, older box scores ~18 h, then PBP; total is several days. Old seasons go to `data/history/nba_history.duckdb`, never `nba.duckdb`. Restart after reboot with the same script (done steps are skipped). The daily and lineup jobs yield stats.nba.com to it (b39aed0) |

Guards: one job at a time (`data/ops/lock`), waits up to 15 min for another `nba.duckdb` writer, failures go to `data/ops/ALERTS.md` plus a macOS notification. The Mac must be awake. Debutants on official-roster runs get a capped, cached `players_static` auto-fill that never fails the run (b39aed0). Details: DAILY_PIPELINE.md.

## 3. Shadow-logged (no effect on production or parlays)

- Integer-support count-stat quantiles (`--log-int-variant`, rows `props_context_residual_int`): pre-registered in fce2e13, code d5df57a. 2023 OOF descriptive CRPS: reb -0.0115, ast -0.0166, fg3m -0.0243, pts -0.0053 (T124-T127). P(>=N) and threshold log loss are exactly unchanged, so it is a scoring-convention gain, not model skill. Naive 80% intervals over-cover once rounded (0.88-0.93); use continuity-corrected coverage.
- T-30 lineups (`props_context_residual_t30`): offline estimate was pts -0.023..-0.041, reb -0.009..-0.018, ast conditional, fg3m fails (T085-T090, T096-T097; WOUNDED because box-score starters stand in for the announced five). The collector will measure the real announcement times.
- Lower-tail short-minutes mixture for pts (`props_context_residual_lt`): passed 2023/2024 (T136-T159, dCRPS -0.011), shadow-logged live; scored under FORWARD_PREREG_2026_27.
- Parlay engine shadow log: marginal/copula parlays, no stake, no orders. Market-deferral gate needs 30 settled rows and >= 14 distinct settled dates (971e0e6).
- Sharp-line benchmark: every pre-tip run stores Pinnacle/US-book game lines and (from opening night) player props next to the Kalshi snapshots; scoring rule drafted in docs/prereg/ODDS_BENCHMARK.md, first look at 1,000 pairs and 30 slate dates per stat.

## 4. Experiments ledger (newest first; ids in TEST_LEDGER.md)

- F9b young top-10 pick prior, re-registered (T186-T201, 2026-10-10): CLOSED. All five data checks passed; no stat reached the slice dCRPS floor (pts -0.003 / -0.006 vs -0.02); the placebo moved the bias as much as pedigree. The under-forecast of young starters (+0.4 pts) is role, not draft slot. No F9c.
- F19 "never four givens" (descriptive, 2025-26 replay with Kalshi prices): the market's four safest sides sweep 40.5% of nights vs 37.4% implied, CI includes 0; a rolled 4-leg ticket returned about -5% after fees. Priced right. reports/f19_safest_legs.md.
- F12 closing-lineup risk (T185) and F9 (T184): closed at their own data checks (F12 premise not replicated; F9 mis-specified undrafted as missing).
- Minutes v2 (T162-T173): minutes-only result, props unchanged. T-30 injury-Elo (T160-T161): null. Hustle screens (T174-T175): null. Lower-tail pts mixture (T136-T159): KEPT as shadow. Upper tail (T129-T135): no candidate.
- Integer quantiles on production (T124-T127): KEPT as shadow, awaiting a decision on the 2025 touch.
- Tracking feature screen, passing/shooting/rebound-chances (T119-T123, 0041bca): NOT KEPT, no family passes (reb rebound-chances -0.0013, a quarter of the floor). Hustle families not run yet.
- Leak-free experiment 3 per-stat hybrid (T102-T118): BROKEN for reb/ast/fg3m (integer-support artifact), WOUNDED for pts (-0.0078 [-0.0128, -0.0029], low-minute rows, reverses in playoffs). NOT KEPT. No 2025 touch.
- Ridge v2, experiment 4 (T098-T101, ab2a72d): NOT KEPT; the exp-2 ridge gain was the missingness leak.
- Lineups known, T-30 (T085-T090, T096-T097): WOUNDED; shadow collector built.
- Basketball GPT / pbp_gpt (T082-T084, 62a9dd7): NOT KEPT.
- Joint same-game set transformer (a592d82): NOT KEPT, ties the Gaussian copula on joint log loss.
- ctxres_v2 exp1/exp2 (042790a, 5940e29): NOT KEPT; exp-2 "ridge gain" later shown to be a leak (voided in f1fcb91).
- Earlier: injury_elo (KEPT, production), context_residual (KEPT, production), possession sim (rebounds and cold-start slices only; no win-prob value), game-context features, time decay, matchup 3A, archetype lineups (not kept). See RESULTS_2026-10-08.md.

## 5. Known risks and open issues

- No clean holdout: season=2025 is burned (FDR_AUDIT 2026-10-08). Live 2026-27 forward results are the only confirmatory data; thin early on.
- Statistical power: win-prob gains are small (log loss -0.008); CIs are close to zero at the lower end.
- Platt P(play) layer behind `p_ge_uncond` has no confirmatory test; check forward before trusting it in parlays. About 36% of rotation DNPs never appear on any injury snapshot (backfill gap).
- Opening-week rosters: official-roster flag raises coverage 61% to 96% in replay (an upper bound); rookies need `players_static` rows.
- Schedule lookahead leak in `sch_days_to_next` / `sch_b2b_first` (postseason): fixed in research v2 features only (c9b00d1), effect <= 0.0005.
- Not rehearsed end to end: live schedule fetch and injury-PDF fetch under launchd need one supervised live run.
- NBA Cup knockout games are dropped from the slate and Elo (minor).
- stats.nba.com quota (~600 requests/h) bounds all backfills; injury PDFs exist only from 2018-12-20, so long-window training needs an era flag.
- Edge versus the market is unproven. Games: the market beats us (2025-26 replay, Brier +0.0064). Props: never compared to a price yet; the historical pull (2023-25, the-odds-api.com) and the live Pinnacle capture (theoddsapi.com) now make the comparison possible, under the two drafted rules.
- Mac is the single runtime; sleep or reboot pauses jobs and the ingest queue. launchd skipped two pretip calendar slots on 2026-10-09 for no visible reason (scheduler change proposed).
- `possessions.oreb` over-counts offensive rebounds about 1.35x vs box scores and the possession parser orders events by `action_number` (not game order): both recorded, both ahead of F11/F13/F15/F17, not needed by F8 or production.
- Odds vendors are new dependencies: theoddsapi.com archive begins 2026-05-13 and Pinnacle posts no preseason lines; player-name resolution relies on a generated alias table (99% of rows) plus a reviewed list.

## 6. Decisions waiting on the maintainer

Current list, with detail in MORNING_BRIEFING_2026-10-10.md:
1. F8 lineup rebounding: five decisions before the rule freezes (fix the rebound flag first or freeze without it; big threshold; order vs F11; motivating-evidence season; bigs-only primary).
2. Confirm the two odds comparison rules (docs/prereg/ODDS_BENCHMARK.md live; ODDS_HISTORY.md historical) before any model row meets a price.
3. Pretip scheduler: switch to a fixed 30-minute interval with the window in the script, and let the watchdog kickstart a stale job.
4. theoddsapi.com: keep as the live benchmark ($99/mo, cancel after the season) or refund within the 7-day window.
5. Google Drive: delete `gdrive:nba_colab/ridge_v2_sweep` (4.4 GB; ridge v2 NOT KEPT).
6. Oracle VM after opening night; own rclone client_id (shared one retires in 2026).
7. Integer quantiles: one-time 2025 holdout touch versus letting the live shadow decide.

## 7. Opening-night checklist (first games 2026-10-20)

See NEXT_SESSION.md for the dated list.

## 8. Roadmap after opening night

See NEXT_SESSION.md.

---

# Background (2026-10-08 narrative, still accurate for the sections below)

## Results as of 2026-10-08 — the sim earned its first player-prop wins
Gave the possession sim **real 5-man lineups** (parsed for 100% of 1.05M possessions) and **real
player attributes** (position/height/weight/draft/age, all 891 players). Result: the sim now
**beats the season-average baseline on rebounds** (CRPS −0.014, CI excludes 0 — first prop stat to
do so), **ties** on points/assists, all biases within ±0.5. A **routing analysis** shows the sim's
edge is **concentrated in cold-start / intermittent / erratic players** (it wins those buckets with
CI excluding 0; season-average only wins "smooth" high-volume regulars) — a concrete registry
routing rule and direct confirmation of the cold-start thesis. Two ideas tested and honestly set
aside: on-court usage denominator (regressed → off) and lineup archetype-MIX (real but negligible,
R²≈0.016 → no rung-4 encoder yet). Full tables + caveats in RESULTS_2026-10-08.md.

## What we're building
A system that predicts NBA games two ways: **who wins** (and by how much), and **individual player stat lines** (points, rebounds, assists, threes). The guiding principle isn't "be right more often" — it's **be honestly calibrated**: when the model says 70%, it should happen ~70% of the time. At every step we check against real history and against dead-simple benchmarks, so we don't fool ourselves.

## North-star goal (set 2026-10-07)
The project's **primary objective is now the possession-level simulation engine** — a *generative model of the game* (CLAUDE.md's original mission), replacing feature-ML as the center of gravity. Its step-models are to be learned with **neural / attention methods** (transformers over possession/event sequences; permutation-invariant set-attention over 5-man lineups for cold-start), producing calibrated full-game **and** player-line distributions, finished with an **ensemble + isotonic/Platt calibration** layer (ladder rungs 3→4→5→6).

**Why the pivot is earned:** feature-ML has demonstrably plateaued — nothing (fancier models, game-context, minutes context) beats Elo / season-average; even the one win (GA-tuned MOV-Elo) is tiny. The sim is the only structurally different path, and the only place deep models + real information (availability, matchup, lineups) get *used* rather than re-approximating Elo.

**Critical path:** PBP pull → possession/stint parser (time-boxed, reconciliation-gated per risk #5) → possession-outcome step-models → Monte Carlo sim → neural step-heads + lineup set-attention (GPU/Colab enters here) → ensemble + calibrate. GPU training (Colab) was deferred to exactly these rungs.

## One-sentence headline
We have a clean, working, honest system on 4 real seasons — and what it's telling us is that NBA games and player stats are **hard to predict better than simple methods already do.** That's a real finding, not a failure.

## What "the Elo model" really means
Elo is a team-strength number borrowed from chess. Every team has a rating (avg ≈ 1500). The winner of a game takes rating points from the loser — more for beating a strong team than a weak one. Over a season each number drifts to reflect how good the team is. To predict a game we take the two ratings, add a home-court bump, and convert the gap into a win probability.

Why it matters that Elo is our **best** model: we built fancier models (logistic regression, LightGBM) with dozens of features and **none beat Elo.** The lesson: almost all the predictable signal in an NBA game is just "which team is better, adjusted for home court and recent form," and one simple self-updating number captures that about as well as anything.

- Elo gets the winner right **~64%** of the time and is **well-calibrated**.
- Reference points: always-home ≈ 55%; a sharp Vegas line ≈ 68–70%.

## Status by front
| Front | Status | Plain-English result |
|---|---|---|
| Data / ingestion | ✅ solid | 4 seasons, ~5,300 games, ~138k player-games, all free NBA API, local. 3 real data bugs found & fixed. Trustworthy. |
| Win probability | ✅ done | Elo wins (~64%, calibrated); fancier models don't beat it. |
| Player props | ✅ working | Unbiased; sim **beats season-avg on rebounds** and **ties** pts/ast. Edge is **concentrated in cold-start/volatile players** (routing analysis) — route those to the sim, regulars to season-avg. |
| Game-context features | ✅ tested | Travel / national TV / playoff race / tanking did **not** beat Elo for wins (Elo already encodes team quality). Being tested on player **minutes** next. |
| Markets / parlays | ⬜ not started | No odds data yet → the "is there money here?" question is **unanswered**. |
| Infrastructure | ✅ pro-grade | Tests, CI (green), model registry, reproducible runs. |

## What you KNOW
- NBA winners predictable ~64%, with trustworthy (calibrated) probabilities.
- Simple team-strength (Elo) is the winner; complexity didn't help.
- Player stat predictions are unbiased but **don't beat a season average** (except 3PM / rookies).
- Data + pipeline are clean and reproducible.

## What you DON'T know yet
- **Whether any of this beats the betting market** — the real test of edge. Needs odds data (Phase 3, not built).
- Whether context features help player **minutes** (test in progress).
- ~~Whether a possession-by-possession simulation beats these simple models.~~ **Answered (2026-10-08):** it beats season-avg on rebounds and in cold-start/volatile player slices; ties elsewhere; does not beat Elo on win prob. See RESULTS_2026-10-08.md.
- Real parlay expected value — the honest-EV tool isn't built.

## The honest bottom line
As an **edge-finding / betting** tool: unproven and probably small — simple baselines (Elo, season averages) are hard to beat, which is exactly what the literature predicts. As an **engineering + forecasting** project: genuinely strong — calibrated, leakage-free, well-tested, and honest about its own limits.
