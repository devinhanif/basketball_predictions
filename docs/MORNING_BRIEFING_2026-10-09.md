# Morning briefing — 2026-10-09

Overnight autonomous run (CPU only, no Colab, no 2025 holdout touches, no promotions or production changes).
Updated as work finished; the newest status is at the bottom of each section.

## Headline results

| Experiment | Result | Status |
|---|---|---|
| **Lineups known (T-30)**, T085-T090 | Confirmed starters improve props at a *new* prediction time (30 min before tip): pts CRPS -0.041 [-0.048, -0.035], reb -0.018, ast -0.009; fg3m -0.004 (below the -0.005 floor). Biggest gain for players whose starter status changed (pts -0.24). P(play) log loss -0.054. | Passed its pre-registered rule on 2024. **Proxy caveat:** box-score starters stand in for the announced lineup, so the gain is upward-biased. Red team attacking it overnight. Not wired into production. |
| **Repo-wide leak audit** (116 data sources) | Only the known `ridge_*` leak (ctxres_v2/v3 files). **Production models are clean.** | Done. |
| **Basketball GPT** (last night), T082-T084 | Worse than simple baselines on live win probability at every checkpoint; 2/12 late player-line cells won. | NOT KEPT. |

## Red team: lineups known (docs/reviews/redteam_lineups_known_2026-10-09.md): WOUNDED

The gain is real: shuffling starters kills it, and it holds by seed, month and team. But it's smaller than claimed, because the box-score five already "knows" how game-time decisions turned out.

Range from worst case to headline:

| Stat | Gain range (CRPS) | Verdict |
|---|---|---|
| pts | −0.023 to −0.041 | **passes** |
| reb | −0.009 to −0.018 | **passes** |
| ast | −0.005 to −0.009 | conditional (at the floor in the worst case) |
| fg3m | — | fails |

P(play) was ~2× overstated once the baseline gets the T-60 OUT report (T096–T097).

**To settle the range:** a T-30 collector would record when lineups and the inactive list are actually announced, then measure how many game-time decisions are still open at that moment. Still worth building for pts and reb, but it's your call.

## Red team: production models (docs/reviews/redteam_production_2026-10-09.md)

- **`rung0_injury_elo`: SURVIVES.** The gain disappears when injury features are shuffled, it isn't built from who actually sat (an oracle built from real DNPs is 4× weaker), and it holds by season, seed and month.
- **`props_context_residual`: WOUNDED (minor).**
  - **The defect:** backtests gate the injury report on a 7 pm ET tip-off proxy. For the ~8% of games that tip at or before 5 pm, that admits the 5 pm report even though it was published after tip-off. Live serving already uses real tip times, so this only affects backtests and training data.
  - **Corrected 2022–24 gains:** pts −0.086 (was −0.089), reb −0.031, ast −0.023, fg3m −0.010. All still well past the floor.
  - **Holdout:** the 2025 pts figure is probably overstated by ~0.003. It won't be re-touched.
- **Fixed (90ecefb):** backtests can now gate on real tip times (`tip_source=real`; covers 3,953/3,953 games from 2022 to 2024). Re-run as T091–T095: Elo −0.0081 (unchanged), pts −0.086, reb −0.031, ast −0.023, fg3m −0.010. A holdout-log notice was appended. **Your decision:** switch production *training* to real tips as well. The live path already uses them, so this removes a train/serve mismatch; it costs ~0.003 of the pts gain and needs a retrain plus re-register.
- Other notes:
  - ~36% of rotation-player DNPs never appear on any injury report snapshot (missing team sections in the backfill).
  - The Platt P(play) layer has no confirmatory test yet. Check it forward before trusting `p_ge_uncond` in parlays.

## Ridge v2 (experiment 4): you ran it, it's scored. NOT KEPT (T098–T101)

- **The leak explained all of experiment 2's "ridge gain"** (pts +0.068, reb +0.043, ast +0.014, fg3m +0.007). The leak-free ridge adds ~0, and none of the ~20 new variants helps.
- **Descriptive follow-up:** leak-free v2 features vs production on the same 2024 rows: pts −0.007, reb −0.008, **ast −0.016, fg3m −0.025**. So the v2 set still has real signal for assists and threes, but its pts and reb gains were mostly leak.
- **Next:** a leak-fixed experiment 3 with a fresh pre-registration. 2024 now counts as seen, so confirmation has to come from a holdout touch or the live season.
- **Colab:** the ridge v2 L4 session is done, so please terminate it.

## Integer-support quantiles: ready for your approval (fce2e13 prereg, d5df57a code; T124–T127)

Rounding production's count-stat quantiles to whole numbers (`ceil(q − 0.5)`) improves CRPS on 2023 too:

| Stat | Change in CRPS |
|---|---|
| reb | −0.0115 |
| ast | −0.0166 |
| fg3m | −0.0243 |
| pts (descriptive only) | −0.005 |

- **P(≥N) and threshold log loss are exactly unchanged,** so parlay probabilities don't move. This is a scoring-convention gain, not model skill.
- **Caveat:** naive 80% intervals over-cover once rounded (0.88–0.93), so report continuity-corrected coverage.

**Your options:**
- **(a)** approve the one-time 2025 holdout touch. The draft row is in `docs/INTEGER_QUANTILES.md`. Your approval, logged before it runs, is required.
- **(b)** turn on `--log-int-variant` from opening night, logging both variants live, and let 2026-27 decide.
- **(c)** both.

Flags default off; production is unchanged.

## Tracking feature screen: NO SIGNAL (T119–T123, 0041bca)

Tracking data for all 3,953 games of 2022–24 is loaded into `nba.duckdb`; the leak audit is clean. Results by family, tested against production props on 2024:

| Family → stat | ΔCRPS [CI] | Verdict |
|---|---|---|
| passing → ast | −0.0002 [−0.0011, +0.0009] | no |
| passing → pts | +0.0016 | no |
| shooting → pts | +0.0018 | no |
| shooting → fg3m | +0.0003 | no |
| rebound chances → reb | −0.0013 [−0.0022, −0.0004] | significant, but only ¼ of the floor |

**Reading:** the recency box-score averages already carry what per-game tracking rates add. This fits the pattern so far: the gains come from **new pre-tip information**, like the injury report and lineups, not from richer descriptions of past games. The hustle families (box-outs, deflections) run once hustle finishes downloading (~6 h); expect the same.

## Leak-free experiment 3: replicated on paper, then BROKEN by the red team (T102–T118)

- **On paper:** it beat production on all 4 stats in 2023 and 2024.
- **Counts were a scoring artifact.** The red team found the reb/ast/fg3m wins came from the new model outputting whole numbers while production outputs continuous values. Rounding production's quantiles erases the gap: reb is then worse, and ast and fg3m fall below the floor.
- **pts is WOUNDED:** −0.008, real but small. It comes mostly from the model type, sits mostly on players who barely play, and reverses in the playoffs.
- **The good news: a FREE improvement for production.** Rounding its count-stat quantiles to whole numbers improves CRPS by reb −0.011, ast −0.016, fg3m −0.023, with P(≥N) essentially unchanged.
  - It needs its own small pre-registration, because 2024 is now seen.
  - It's a strong candidate for your approval before Oct 20.
- **New best practice:** score count stats on whole-number support for every model. This likely also explains experiment 2's "NB wins counts".
- **Minor leak found:** two schedule features (`sch_days_to_next`, `sch_b2b_first`) look ahead in the postseason. The effect is immaterial (≤0.0005) but should be fixed.
- **Recommendation:** no 2025 touch for experiment 3. If anything, a pts-only, regular-season version with an upper-tail fix.

## Opening-night dress rehearsal (replayed 2024-10-22..24 on a DB copy)

**Go** for the daily path: predictions, after-tip leakage guards, settle, report, parlay shadow log and assistant tools. Each run took ~10 s and peaked at ~0.9 GB. Details: `docs/DRESS_REHEARSAL_2026-10-09.md`.

Bugs found and fixed (with tests):
- Parlay legs on players missing from a final box score stayed "open" forever (~6% of shadow rows). They now settle as DNP. (59cf0f6)
- The shadow track record counted both YES and NO sides, which doubled the sample size. (59cf0f6)
- **The market-deferral gate could open after a single night of results.** It now also needs ≥14 distinct settled dates on top of 30 rows. (971e0e6)

Open issues:
- **Stale opening-week rosters: FIXED behind a flag (0e1775f, docs/OPENING_WEEK_ROSTERS.md).**
  - `--roster-source official` combines official team rosters with recent players.
  - Coverage of players who played on days 1–3 rose from **61% to 96%**: debutants 0 → 94%, players who moved teams 4 → 92%.
  - The rookie prior cut debutant minutes error by 6–7 min.
  - Props quality on players covered both ways is unchanged.
  - Caveat: measured with an optimistic replay roster, so treat coverage as an upper bound.
  - **Your decision:** turn it on for the first ~2 weeks (`nba.daily run --roster-source official`; the ops script would need that flag). The code default stays `recent` until a live night confirms it.
  - 2026 rookies still need a small `players_static` pull (~60–80 calls) for their draft slots.
- NBA Cup knockout games are dropped from the slate and from Elo. Minor.
- Not rehearsed: the live schedule and injury-PDF fetch, and the launchd job. They need one supervised live run before Oct 20.

## Pending overnight (see sections below as they land)
- Tracking/hustle feature screen (pre-registered, commit c88788a): waiting on the tracking download.
- Opening-night dress rehearsal (replays 2024 opening night through the whole forward pipeline).
- Red team: both production models; the lineups-known win.
- Older seasons (2013-14 to 2021-22) download into a separate history DB.

## Data download: the real constraint

stats.nba.com enforces a rolling quota of about **600 requests per hour**, then blocks for 30+ minutes. Faster pacing doesn't help. Overnight this made tracking progress slow: 2,000 of 5,269 games by 00:45. The ingest queue now paces at ~6 s per request with an escalating cooldown and pulls the 2022–24 seasons first, since the screen doesn't need 2025.

At ~600 per hour, the full wish list (current-season sources plus nine older seasons of games, box scores, play-by-play and tracking) is **several days of background pulling**. Budget it accordingly: tracking 2022–24 needs ~4 h, hustle ~6.5 h, older box scores ~18 h, and so on. Status: `data/ops/ingest_queue_status.md`.

**Queue update (07:04 UTC):**
- Running at ~610 requests/hour with **zero failures** since the 6 s pacing.
- Tracking 2022–24 is ~2 h from done, then hustle ~6.5 h, then the older seasons.
- Older seasons go to `data/history/nba_history.duckdb`, never `nba.duckdb`.
- Restart after a reboot with `sh ops/run_ingest_queue.sh`; finished steps are skipped.

**Injury reports for older seasons:**
- Official PDFs exist only from **2018-12-20 onward**.
- Seasons 2013-14 to 2017-18 have no pre-tip availability feed, so a longer training window would mix rows with and without injury data. That's exactly the missingness trap, so the window experiment must handle it explicitly, e.g. an era flag, or train on ≥2019 when using injury features.

## Ops and housekeeping
- Off-machine backup now exists: `gdrive:nba_backups/` holds the registry and the latest DB copy (~204 MB). The morning job refreshes it nightly once the scheduler is installed.
- Kalshi name matching: all 20 player names in current markets resolve. A 497-player review file is ready for opening-night markets (`configs/kalshi_aliases_candidates_2026-10-09.yaml`); 9 "Jr./II" name collisions need a human pick.
- Cost: at least 5.4 GPU-hours used yesterday (T4 1.9, L4 0.8, A100 2.65). Please paste a fresh Colab unit balance. About 3.15 GB of finished Colab checkpoints on Drive can be deleted (commands in `docs/COST_REPORT.md`; nothing deleted).
- **rclone's shared Google Drive client ID is being retired in 2026.** Create your own (~10 min) or Colab transfers will break.

## Your decisions this morning
1. Ridge v2: did it run? If "ARTIFACTS WRITTEN TO" printed, tell me and I'll evaluate.
2. Install the scheduler on the Mac (`sh ops/install_launchd.sh`) before Oct 20, or decide on the VM.
3. T-30 lineups: worth building a lineup collector and a second daily run? (Wait for the red-team verdict.)
4. Approve deleting the ~3.15 GB of finished Colab checkpoints.
